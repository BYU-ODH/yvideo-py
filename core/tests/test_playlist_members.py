"""Playlist assignment endpoints and their permission boundaries.

Student and collaborator assignments use separate endpoints. Owners may grant either
role; collaborators may manage only students and cannot overwrite existing memberships
through the student-assignment endpoint.

`login` rather than force_login, for the SessionRefresh reason spelled out in
test_permissions_regressions.py.
"""

import re
import time
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.factories import PlaylistFactory
from core.factories import PlaylistUserAccessFactory
from core.factories import UserFactory
from core.models import PlaylistRole
from core.models import PlaylistUserAccess


def login(client, user):
    client.force_login(user)
    session = client.session
    session["oidc_id_token_expiration"] = time.time() + 3600
    session.save()


class PlaylistMemberTestCase(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.playlist = PlaylistFactory(owner=self.owner, published=True)
        self.collaborator = UserFactory(instructor=True)
        PlaylistUserAccessFactory(
            user=self.collaborator,
            playlist=self.playlist,
            playlist_role=PlaylistRole.COLLABORATOR,
        )
        self.student = UserFactory(student=True)
        PlaylistUserAccessFactory(
            user=self.student,
            playlist=self.playlist,
            playlist_role=PlaylistRole.STUDENT,
        )
        self.outsider = UserFactory(student=True)

    def add_url(self):
        return reverse("add_playlist_member", args=[self.playlist.pk])

    def collaborator_add_url(self):
        return reverse("add_playlist_collaborator", args=[self.playlist.pk])

    def remove_url(self, user):
        return reverse("remove_playlist_member", args=[self.playlist.pk, user.pk])

    def role_of(self, user):
        return PlaylistUserAccess.objects.get(
            user=user, playlist=self.playlist
        ).playlist_role


class PlaylistAssignmentListTests(PlaylistMemberTestCase):
    def playlist_page(self, user):
        login(self.client, user)
        response = self.client.get(reverse("playlist_info", args=[self.playlist.pk]))
        self.assertEqual(response.status_code, 200)
        return response

    def test_empty_collaborators_do_not_show_students(self):
        PlaylistUserAccess.objects.filter(
            playlist=self.playlist, playlist_role=PlaylistRole.COLLABORATOR
        ).delete()

        response = self.playlist_page(self.owner)

        self.assertContains(response, "No collaborators assigned.")
        self.assertNotContains(response, "No individuals assigned.")
        self.assertContains(
            response, f'data-remove-url="{self.remove_url(self.student)}"', count=1
        )

    def test_removal_buttons_preserve_membership_permissions(self):
        for viewer in (self.owner, self.collaborator):
            response = self.playlist_page(viewer)
            buttons = re.findall(r"<button\b[^>]*>", response.content.decode())
            for member in (self.student, self.collaborator):
                with self.subTest(viewer=viewer, member=member):
                    button = next(
                        button
                        for button in buttons
                        if f'data-remove-url="{self.remove_url(member)}"' in button
                    )
                    self.assertEqual(
                        "disabled" in button,
                        viewer == self.collaborator and member == self.collaborator,
                    )


class OwnerManagesEveryoneTests(PlaylistMemberTestCase):
    def setUp(self):
        super().setUp()
        login(self.client, self.owner)

    def test_the_owner_may_grant_each_role_through_its_assignment_endpoint(self):
        for url, role in (
            (self.collaborator_add_url(), PlaylistRole.COLLABORATOR),
            (self.add_url(), PlaylistRole.STUDENT),
        ):
            with self.subTest(role=role):
                newcomer = UserFactory(student=True)
                response = self.client.post(url, {"user_id": newcomer.pk})
                self.assertEqual(response.status_code, 204, response.content[:200])
                self.assertEqual(response.content, b"")
                self.assertEqual(self.role_of(newcomer), role)

    def test_the_owner_sees_individuals_and_collaborators_in_their_own_lists(self):
        response = self.client.get(reverse("playlist_info", args=[self.playlist.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [access.user for access in response.context["individual_accesses"]],
            [self.student],
        )
        self.assertEqual(
            [access.user for access in response.context["collaborator_accesses"]],
            [self.collaborator],
        )

    def test_the_owner_may_promote_a_student_to_collaborator(self):
        response = self.client.post(
            self.collaborator_add_url(), {"user_id": self.student.pk}
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.role_of(self.student), PlaylistRole.COLLABORATOR)

    def test_the_owner_may_reassign_a_collaborator_as_a_student(self):
        removal = self.client.post(self.remove_url(self.collaborator))
        self.assertEqual(removal.status_code, 204)
        self.assertFalse(
            PlaylistUserAccess.objects.filter(
                playlist=self.playlist, user=self.collaborator
            ).exists()
        )

        response = self.client.post(self.add_url(), {"user_id": self.collaborator.pk})

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.role_of(self.collaborator), PlaylistRole.STUDENT)

    def test_the_owner_may_remove_a_collaborator(self):
        response = self.client.post(self.remove_url(self.collaborator))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            PlaylistUserAccess.objects.filter(
                user=self.collaborator, playlist=self.playlist
            ).exists()
        )

    def test_adding_someone_twice_reports_it_rather_than_erroring(self):
        response = self.client.post(
            self.add_url(),
            {"user_id": self.student.pk, "role": PlaylistRole.STUDENT.value},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"already has a role", response.content)
        self.assertEqual(
            PlaylistUserAccess.objects.filter(
                user=self.student, playlist=self.playlist
            ).count(),
            1,
        )

    def test_the_owner_cannot_be_added_as_their_own_member(self):
        response = self.client.post(
            self.add_url(),
            {"user_id": self.owner.pk, "role": PlaylistRole.COLLABORATOR.value},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            PlaylistUserAccess.objects.filter(
                user=self.owner, playlist=self.playlist
            ).exists()
        )

    def test_an_owner_holding_their_own_access_row_is_listed_once(self):
        """dev_seed and the legacy importer both write one, so this is not hypothetical."""
        PlaylistUserAccessFactory(
            user=self.owner,
            playlist=self.playlist,
            playlist_role=PlaylistRole.COLLABORATOR,
        )
        response = self.client.get(reverse("playlist_info", args=[self.playlist.pk]))
        listed = [
            access.user_id
            for access in response.context["individual_accesses"]
            + response.context["collaborator_accesses"]
        ]
        self.assertNotIn(self.owner.pk, listed)

    def test_the_owners_own_access_row_cannot_be_removed_as_a_membership(self):
        PlaylistUserAccessFactory(
            user=self.owner,
            playlist=self.playlist,
            playlist_role=PlaylistRole.COLLABORATOR,
        )
        response = self.client.post(self.remove_url(self.owner))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.role_of(self.owner), PlaylistRole.COLLABORATOR)

    def test_the_individual_endpoint_always_assigns_the_student_role(self):
        for role in (3, PlaylistRole.COLLABORATOR.value):
            with self.subTest(role=role):
                newcomer = UserFactory(student=True)
                response = self.client.post(
                    self.add_url(), {"user_id": newcomer.pk, "role": role}
                )

                self.assertEqual(response.status_code, 204)
                self.assertEqual(self.role_of(newcomer), PlaylistRole.STUDENT)


class CollaboratorIsHeldToReadOnlyGrantsTests(PlaylistMemberTestCase):
    """A collaborator may manage students and nothing else -- the promotion ceiling from #372."""

    def setUp(self):
        super().setUp()
        self.other_collaborator = UserFactory(instructor=True)
        PlaylistUserAccessFactory(
            user=self.other_collaborator,
            playlist=self.playlist,
            playlist_role=PlaylistRole.COLLABORATOR,
        )
        login(self.client, self.collaborator)

    def test_a_collaborator_may_add_a_student(self):
        response = self.client.post(
            self.add_url(),
            {"user_id": self.outsider.pk, "role": PlaylistRole.STUDENT.value},
        )
        self.assertEqual(response.status_code, 204, response.content[:200])
        self.assertEqual(self.role_of(self.outsider), PlaylistRole.STUDENT)

    def test_a_collaborator_may_remove_a_student(self):
        response = self.client.post(self.remove_url(self.student))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            PlaylistUserAccess.objects.filter(
                user=self.student, playlist=self.playlist
            ).exists()
        )

    def test_a_collaborator_may_not_grant_the_collaborator_role(self):
        response = self.client.post(
            self.collaborator_add_url(), {"user_id": self.outsider.pk}
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            PlaylistUserAccess.objects.filter(
                user=self.outsider, playlist=self.playlist
            ).exists()
        )

    def test_a_collaborator_may_not_promote_a_student(self):
        response = self.client.post(
            self.collaborator_add_url(), {"user_id": self.student.pk}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.role_of(self.student), PlaylistRole.STUDENT)

    def test_a_collaborator_may_not_demote_another_collaborator(self):
        """The student-assignment endpoint cannot overwrite an existing collaborator."""
        response = self.client.post(
            self.add_url(), {"user_id": self.other_collaborator.pk}
        )
        self.assertEqual(response.status_code, 400)
        self.assertFormError(
            response.context["assign_individual_form"],
            "user_id",
            "This person already has a role on this playlist.",
        )
        self.assertEqual(
            self.role_of(self.other_collaborator), PlaylistRole.COLLABORATOR
        )

    def test_a_collaborator_may_not_remove_another_collaborator(self):
        response = self.client.post(self.remove_url(self.other_collaborator))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            PlaylistUserAccess.objects.filter(
                user=self.other_collaborator, playlist=self.playlist
            ).exists()
        )

    def test_a_collaborator_cannot_search_for_collaborator_assignments(self):
        response = self.client.post(
            reverse("playlist_collaborator_search", args=[self.playlist.pk]),
            {"search": self.outsider.username},
        )
        self.assertEqual(response.status_code, 403)


class OutsidersAreRefusedTests(PlaylistMemberTestCase):
    def test_a_student_member_does_not_see_who_else_has_access(self):
        login(self.client, self.student)
        response = self.client.get(reverse("playlist_info", args=[self.playlist.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("individual_accesses", response.context)
        self.assertNotContains(response, self.collaborator.get_full_name())

    def test_a_stranger_cannot_add_anyone(self):
        login(self.client, self.outsider)
        response = self.client.post(
            self.add_url(),
            {"user_id": self.outsider.pk, "role": PlaylistRole.COLLABORATOR.value},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            PlaylistUserAccess.objects.filter(
                user=self.outsider, playlist=self.playlist
            ).exists()
        )

    def test_a_member_of_another_playlist_is_not_reachable_here(self):
        """The row is looked up by (playlist, user), so ids from elsewhere 404."""
        login(self.client, self.owner)
        elsewhere = PlaylistUserAccessFactory(playlist_role=PlaylistRole.STUDENT)
        response = self.client.post(self.remove_url(elsewhere.user))
        self.assertEqual(response.status_code, 404)


class MemberSearchTests(PlaylistMemberTestCase):
    def setUp(self):
        super().setUp()
        login(self.client, self.owner)

    def search(self, query):
        response = self.client.post(
            reverse("playlist_member_search", args=[self.playlist.pk]),
            {"search": query},
        )
        return response.context["users"]

    def test_search_finds_someone_by_last_name(self):
        target = UserFactory(student=True, last_name="Winterbourne")
        self.assertIn(target, self.search("winterb"))

    def test_search_hides_people_already_on_the_playlist(self):
        self.student.last_name = "Winterbourne"
        self.student.save()
        self.assertNotIn(self.student, self.search("winterb"))

    def test_search_hides_the_owner(self):
        self.owner.last_name = "Winterbourne"
        self.owner.save()
        self.assertNotIn(self.owner, self.search("winterb"))

    def test_an_empty_query_matches_no_one(self):
        self.assertEqual(len(self.search("")), 0)

    def test_a_single_character_finds_matches_for_both_assignment_searches(self):
        target = UserFactory(student=True, last_name="Winterbourne")
        for url_name in ("playlist_member_search", "playlist_collaborator_search"):
            with self.subTest(url_name=url_name):
                response = self.client.post(
                    reverse(url_name, args=[self.playlist.pk]), {"search": "w"}
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(target, response.context["users"])

    def test_two_characters_is_enough_to_search(self):
        target = UserFactory(student=True, last_name="Winterbourne")
        self.assertIn(target, self.search("wi"))

    def test_a_whitespace_only_query_matches_no_one(self):
        self.assertEqual(len(self.search("   ")), 0)


class AddingSelectedPersonTests(PlaylistMemberTestCase):
    def setUp(self):
        super().setUp()
        login(self.client, self.owner)

    def add(self, **payload):
        return self.client.post(
            self.add_url(), {"role": PlaylistRole.STUDENT.value, **payload}
        )

    def test_a_missing_selection_asks_for_a_person(self):
        initial_count = self.playlist.playlistuseraccess_set.count()
        with patch("core.forms.Api") as api:
            response = self.add()

        self.assertEqual(response.status_code, 400)
        self.assertFormError(
            response.context["assign_individual_form"],
            "user_id",
            "Select a person from the search results.",
        )
        self.assertEqual(self.playlist.playlistuseraccess_set.count(), initial_count)
        api.assert_not_called()

    def test_a_user_id_naming_a_deleted_account_is_refused(self):
        doomed = UserFactory(student=True)
        doomed_pk = doomed.pk
        doomed.delete()

        initial_count = self.playlist.playlistuseraccess_set.count()
        response = self.add(user_id=doomed_pk)

        self.assertEqual(response.status_code, 400)
        self.assertFormError(
            response.context["assign_individual_form"],
            "user_id",
            "Select a valid person from the search results.",
        )
        self.assertEqual(self.playlist.playlistuseraccess_set.count(), initial_count)
