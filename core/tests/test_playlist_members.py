"""The Manage People endpoints (#361).

The rule under test throughout is Playlist.can_grant_role: an owner hands out any role,
a TA or co-instructor hands out only read-only ones. The cases that matter are the ones
where a TA reaches past that -- promoting, demoting, or removing someone at their own
level -- because the decorator alone lets them through and only the inner check stops
them.

`login` rather than force_login, for the SessionRefresh reason spelled out in
test_permissions_regressions.py.
"""

import time
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from core.factories import CourseFactory
from core.factories import PlaylistFactory
from core.factories import PlaylistUserAccessFactory
from core.factories import UserCourseFactory
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
        self.ta = UserFactory(instructor=True)
        PlaylistUserAccessFactory(
            user=self.ta, playlist=self.playlist, playlist_role=PlaylistRole.TA
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

    def role_url(self, user):
        return reverse("update_playlist_member_role", args=[self.playlist.pk, user.pk])

    def remove_url(self, user):
        return reverse("remove_playlist_member", args=[self.playlist.pk, user.pk])

    def role_of(self, user):
        return PlaylistUserAccess.objects.get(
            user=user, playlist=self.playlist
        ).playlist_role


class OwnerManagesEveryoneTests(PlaylistMemberTestCase):
    def setUp(self):
        super().setUp()
        login(self.client, self.owner)

    def test_the_owner_may_grant_every_offered_role(self):
        for role in (PlaylistRole.INSTRUCTOR, PlaylistRole.TA, PlaylistRole.STUDENT):
            with self.subTest(role=role):
                newcomer = UserFactory(student=True)
                response = self.client.post(
                    self.add_url(), {"user_id": newcomer.pk, "role": role.value}
                )
                self.assertEqual(response.status_code, 200, response.content[:200])
                self.assertEqual(self.role_of(newcomer), role)

    def test_the_owner_may_promote_a_student_to_ta(self):
        response = self.client.post(
            self.role_url(self.student), {"role": PlaylistRole.TA.value}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.role_of(self.student), PlaylistRole.TA)

    def test_the_owner_may_demote_a_ta(self):
        response = self.client.post(
            self.role_url(self.ta), {"role": PlaylistRole.STUDENT.value}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.role_of(self.ta), PlaylistRole.STUDENT)

    def test_the_owner_may_remove_a_ta(self):
        response = self.client.post(self.remove_url(self.ta))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            PlaylistUserAccess.objects.filter(
                user=self.ta, playlist=self.playlist
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
            self.add_url(), {"user_id": self.owner.pk, "role": PlaylistRole.TA.value}
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
            playlist_role=PlaylistRole.INSTRUCTOR,
        )
        response = self.client.get(
            reverse("render_playlist_members", args=[self.playlist.pk])
        )
        listed = [member["user"].pk for member in response.context["members"]]
        self.assertNotIn(self.owner.pk, listed)

    def test_the_owners_own_access_row_cannot_be_edited_as_a_membership(self):
        PlaylistUserAccessFactory(
            user=self.owner,
            playlist=self.playlist,
            playlist_role=PlaylistRole.INSTRUCTOR,
        )
        response = self.client.post(
            self.role_url(self.owner), {"role": PlaylistRole.STUDENT.value}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.role_of(self.owner), PlaylistRole.INSTRUCTOR)

    def test_a_role_outside_the_offered_set_is_refused(self):
        response = self.client.post(
            self.add_url(), {"user_id": self.outsider.pk, "role": 3}
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            PlaylistUserAccess.objects.filter(
                user=self.outsider, playlist=self.playlist
            ).exists()
        )


class TeachingAssistantIsHeldToReadOnlyGrantsTests(PlaylistMemberTestCase):
    """A TA may manage students and nothing else -- the promotion ceiling from #372."""

    def setUp(self):
        super().setUp()
        self.co_instructor = UserFactory(instructor=True)
        PlaylistUserAccessFactory(
            user=self.co_instructor,
            playlist=self.playlist,
            playlist_role=PlaylistRole.INSTRUCTOR,
        )
        login(self.client, self.ta)

    def test_a_ta_may_add_a_student(self):
        response = self.client.post(
            self.add_url(),
            {"user_id": self.outsider.pk, "role": PlaylistRole.STUDENT.value},
        )
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(self.role_of(self.outsider), PlaylistRole.STUDENT)

    def test_a_ta_may_remove_a_student(self):
        response = self.client.post(self.remove_url(self.student))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            PlaylistUserAccess.objects.filter(
                user=self.student, playlist=self.playlist
            ).exists()
        )

    def test_a_refusal_says_why_in_a_body_the_panel_may_show(self):
        """text/plain is how the client tells our message from a Django error page.

        Sent as text/html it would be indistinguishable from a 500's error document, and
        the panel would replace it with a generic failure rather than the reason.
        """
        response = self.client.post(
            self.add_url(), {"user_id": self.outsider.pk, "role": PlaylistRole.TA.value}
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        self.assertIn(b"Only the playlist owner", response.content)

    def test_a_ta_may_not_grant_the_ta_role(self):
        response = self.client.post(
            self.add_url(), {"user_id": self.outsider.pk, "role": PlaylistRole.TA.value}
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            PlaylistUserAccess.objects.filter(
                user=self.outsider, playlist=self.playlist
            ).exists()
        )

    def test_a_ta_may_not_grant_the_co_instructor_role(self):
        response = self.client.post(
            self.add_url(),
            {"user_id": self.outsider.pk, "role": PlaylistRole.INSTRUCTOR.value},
        )
        self.assertEqual(response.status_code, 403)

    def test_a_ta_may_not_promote_a_student(self):
        response = self.client.post(
            self.role_url(self.student), {"role": PlaylistRole.TA.value}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.role_of(self.student), PlaylistRole.STUDENT)

    def test_a_ta_may_not_demote_a_co_instructor(self):
        """The check that needs both ends: the target role here is one a TA may grant."""
        response = self.client.post(
            self.role_url(self.co_instructor), {"role": PlaylistRole.STUDENT.value}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.role_of(self.co_instructor), PlaylistRole.INSTRUCTOR)

    def test_a_ta_may_not_remove_another_ta(self):
        other_ta = UserFactory(instructor=True)
        PlaylistUserAccessFactory(
            user=other_ta, playlist=self.playlist, playlist_role=PlaylistRole.TA
        )
        response = self.client.post(self.remove_url(other_ta))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            PlaylistUserAccess.objects.filter(
                user=other_ta, playlist=self.playlist
            ).exists()
        )

    def test_a_ta_may_not_remove_a_co_instructor(self):
        response = self.client.post(self.remove_url(self.co_instructor))
        self.assertEqual(response.status_code, 403)

    def test_the_panel_offers_a_ta_only_the_student_role(self):
        response = self.client.get(
            reverse("render_playlist_members", args=[self.playlist.pk])
        )
        self.assertEqual(response.status_code, 200)
        roles = response.context["grantable_roles"]
        self.assertEqual([role["value"] for role in roles], [PlaylistRole.STUDENT])

    def test_the_panel_leaves_rows_a_ta_may_not_touch_uneditable(self):
        response = self.client.get(
            reverse("render_playlist_members", args=[self.playlist.pk])
        )
        manageable = {
            member["user"].pk: member["may_manage"]
            for member in response.context["members"]
        }
        self.assertTrue(manageable[self.student.pk])
        self.assertFalse(manageable[self.ta.pk])
        self.assertFalse(manageable[self.co_instructor.pk])


class OutsidersAreRefusedTests(PlaylistMemberTestCase):
    def test_a_student_member_cannot_reach_the_panel(self):
        login(self.client, self.student)
        response = self.client.get(
            reverse("render_playlist_members", args=[self.playlist.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_a_stranger_cannot_add_anyone(self):
        login(self.client, self.outsider)
        response = self.client.post(
            self.add_url(), {"user_id": self.outsider.pk, "role": PlaylistRole.TA.value}
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

    def test_a_single_character_matches_no_one(self):
        """Substring matching over every user makes one character a directory to scrape."""
        UserFactory(student=True, last_name="Winterbourne")
        self.assertEqual(len(self.search("w")), 0)

    def test_two_characters_is_enough_to_search(self):
        target = UserFactory(student=True, last_name="Winterbourne")
        self.assertIn(target, self.search("wi"))

    def test_a_too_short_query_says_so_rather_than_reporting_no_matches(self):
        """ "No matches" would read as "this person has no account" and send the person
        to the directory lookup for someone who is already here."""
        response = self.client.post(
            reverse("playlist_member_search", args=[self.playlist.pk]),
            {"search": "w"},
        )
        self.assertTrue(response.context["query_too_short"])
        self.assertIn(b"Keep typing", response.content)


class AddingByIdentifierTests(PlaylistMemberTestCase):
    """The branch that reaches BYU's directory, and everything guarding it.

    `Api` and `create_user` are stubbed throughout: what is under test is which inputs get
    that far, not what BYU answers. MANUAL_TESTING.md §8 covers the real lookup.
    """

    def setUp(self):
        super().setUp()
        login(self.client, self.owner)

    def add(self, **payload):
        return self.client.post(
            self.add_url(), {"role": PlaylistRole.STUDENT.value, **payload}
        )

    def test_a_malformed_identifier_is_refused_before_any_api_call(self):
        with (
            patch("core.forms.Api") as api,
            patch(
                "yvideo.odhOIDCAuthenticationBackend.OIDCUserAuth.create_user"
            ) as create_user,
        ):
            response = self.add(identifier="zzz!!")

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"BYU ID", response.content)
        api.assert_not_called()
        create_user.assert_not_called()

    def test_neither_a_pick_nor_a_typed_identifier_asks_for_one(self):
        with patch("core.forms.Api") as api:
            response = self.add()

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Search for a person", response.content)
        api.assert_not_called()

    def test_a_netid_that_already_has_an_account_skips_the_directory(self):
        newcomer = UserFactory(student=True, netid="hasacct")

        with patch("core.forms.Api") as api:
            response = self.add(identifier="hasacct")

        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(self.role_of(newcomer), PlaylistRole.STUDENT)
        api.assert_not_called()

    def test_a_netid_lookup_that_finds_nobody_explains_rather_than_erroring(self):
        with patch("core.forms.Api") as api:
            api.return_value.get_student_summary.return_value = None
            response = self.add(identifier="nosuchid")

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"No BYU student record", response.content)

    def test_an_unreachable_directory_is_reported_as_worth_retrying(self):
        with patch("core.forms.Api") as api:
            api.return_value.get_student_summary.side_effect = OSError(
                "connection reset"
            )
            response = self.add(identifier="nosuchid")

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Try again", response.content)

    def test_a_missing_api_setting_is_not_reported_as_worth_retrying(self):
        """The API_NET_ID_IAM_URL case: retrying a misconfiguration never succeeds.

        secret_settings.py is gitignored, so an install whose copy predates a key added to
        secret_settings_template.py fails exactly this way, and "try again in a moment"
        sends the person into a loop with no exit.
        """
        with patch("core.forms.Api") as api:
            api.side_effect = AttributeError(
                "module 'yvideo.secret_settings' has no attribute 'API_NET_ID_IAM_URL'"
            )
            response = self.add(identifier="nosuchid")

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"configuration", response.content)
        self.assertNotIn(b"Try again", response.content)

    def test_a_user_id_naming_a_deleted_account_is_refused(self):
        doomed = UserFactory(student=True)
        doomed_pk = doomed.pk
        doomed.delete()

        response = self.add(user_id=doomed_pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"no longer has an account", response.content)

    def test_a_ta_cannot_use_the_lookup_to_provision_a_co_instructor(self):
        """The role check runs first, so a refused grant never reaches BYU's directory."""
        login(self.client, self.ta)

        with patch("core.forms.Api") as api:
            response = self.client.post(
                self.add_url(),
                {"identifier": "nosuchid", "role": PlaylistRole.INSTRUCTOR.value},
            )

        self.assertEqual(response.status_code, 403)
        api.assert_not_called()

    def test_an_enrollment_warning_reaches_the_client(self):
        """It rides a header so the body stays the roster fragment the client swaps in."""
        newcomer = UserFactory(student=True, netid="warnme")

        def resolve(self_, identifier):
            self_.enrollment_warning = "Some courses may be missing."
            return newcomer, True

        with patch("core.forms.AddUserLookupForm._resolve_netid", resolve):
            response = self.add(identifier="warnme")

        self.assertEqual(response.status_code, 200, response.content[:200])
        self.assertEqual(response["X-Member-Warning"], "Some courses may be missing.")


class CourseAccessSummaryTests(PlaylistMemberTestCase):
    """The summary answers "who can actually see this", so it has to match
    can_be_viewed_by rather than count enrollments some looser way."""

    def setUp(self):
        super().setUp()
        login(self.client, self.owner)

    def summary(self):
        response = self.client.get(
            reverse("render_playlist_members", args=[self.playlist.pk])
        )
        return response.context["course_access"]

    def test_an_unassigned_playlist_reports_no_courses(self):
        self.assertEqual(self.summary(), [])

    def test_enrolled_students_are_counted_once_per_course(self):
        course = CourseFactory()
        self.playlist.courses.add(course)
        for _ in range(3):
            UserCourseFactory(course=course, yearterm=course.yearterm)

        summary = self.summary()
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["name"], f"{course.dept} {course.catalog_number}")
        self.assertEqual(summary[0]["student_count"], 3)

    def test_an_enrollment_from_an_inactive_term_is_not_counted(self):
        course = CourseFactory()
        self.playlist.courses.add(course)
        UserCourseFactory(course=course, yearterm="19951")

        summary = self.summary()
        self.assertEqual(summary[0]["student_count"], 0)
