"""Subtitle tracks are shared the way annotation sets are (#502).

A content shows only the tracks its editors attached. Any instructor with access to the
resource may attach another instructor's track, copy it into their own editable version,
and deleting a track that another video uses transfers it instead of removing it.
"""

import json
import shutil
import tempfile
import time

from django.template.loader import render_to_string
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

from core.factories import ContentFactory
from core.factories import LanguageFactory
from core.factories import PlaylistFactory
from core.factories import PlaylistUserAccessFactory
from core.factories import ResourceAccessFactory
from core.factories import ResourceFactory
from core.factories import ResourceFileFactory
from core.factories import SubtitleFactory
from core.factories import UserFactory
from core.models import Content
from core.models import PlaylistRole
from core.models import Subtitle


def login(client, user):
    client.force_login(user)
    session = client.session
    session["oidc_id_token_expiration"] = time.time() + 3600
    session.save()


class SubtitleSharingTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        media_override = override_settings(MEDIA_ROOT=self.media_root)
        media_override.enable()
        self.addCleanup(media_override.disable)

        self.resource = ResourceFactory()
        resource_file = ResourceFileFactory(resource=self.resource)
        self.owner = UserFactory(
            instructor=True, first_name="Ada", last_name="Lovelace", netid="alovelace"
        )
        self.borrower = UserFactory(instructor=True)
        for user in (self.owner, self.borrower):
            ResourceAccessFactory(user=user, resource=self.resource)
        self.owner_content = ContentFactory(
            playlist=PlaylistFactory(owner=self.owner), resource_file=resource_file
        )
        self.borrower_content = ContentFactory(
            playlist=PlaylistFactory(owner=self.borrower), resource_file=resource_file
        )
        self.track = SubtitleFactory(
            resource=self.resource, owner=self.owner, name="Ada's English"
        )
        self.owner_content.subtitle_tracks.add(self.track)

    def post(self, user, name, *args, **kwargs):
        login(self.client, user)
        return self.client.post(reverse(name, args=args), **kwargs)

    def editor_choices(self, user, content):
        login(self.client, user)
        response = self.client.get(reverse("video_editor", args=[content.pk]))
        self.assertEqual(response.status_code, 200)
        return response.context

    # --- what the player shows -----------------------------------------------------

    def test_a_track_shows_only_on_content_it_was_added_to(self):
        self.assertEqual(
            [track["id"] for track in self.owner_content.get_subtitles()],
            [self.track.pk],
        )
        self.assertEqual(self.borrower_content.get_subtitles(), [])

    # --- adding and removing -----------------------------------------------------

    def test_another_instructor_can_add_a_track_they_do_not_own(self):
        response = self.post(
            self.borrower,
            "attach_subtitle_track",
            self.borrower_content.pk,
            self.track.pk,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.track, self.borrower_content.subtitle_tracks.all())
        self.assertFalse(self.track.can_be_edited_by(self.borrower))

    def test_a_track_from_a_different_resource_cannot_be_added(self):
        other_track = SubtitleFactory(owner=self.borrower)

        response = self.post(
            self.borrower,
            "attach_subtitle_track",
            self.borrower_content.pk,
            other_track.pk,
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.borrower_content.subtitle_tracks.exists())

    def test_an_instructor_without_resource_access_cannot_add_a_track(self):
        private_resource = ResourceFactory(
            checked_out_from_hbll=False, checked_out_from_other_byu_library=False
        )
        ResourceAccessFactory(user=self.owner, resource=private_resource)
        private_track = SubtitleFactory(resource=private_resource, owner=self.owner)
        stranger = UserFactory(instructor=True)
        # Not ContentFactory, which grants the playlist owner resource access.
        stranger_content = Content.objects.create(
            title="Stranger's video",
            playlist=PlaylistFactory(owner=stranger),
            resource_file=ResourceFileFactory(resource=private_resource),
        )

        response = self.post(
            stranger, "attach_subtitle_track", stranger_content.pk, private_track.pk
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(stranger_content.subtitle_tracks.exists())

    def test_removing_a_track_affects_only_that_content_and_clears_its_default(self):
        self.borrower_content.subtitle_tracks.add(self.track)
        self.borrower_content.default_subtitle_track = self.track
        self.borrower_content.save()

        response = self.post(
            self.borrower,
            "detach_subtitle_track",
            self.borrower_content.pk,
            self.track.pk,
        )

        self.assertEqual(response.status_code, 200)
        self.borrower_content.refresh_from_db()
        self.assertFalse(self.borrower_content.subtitle_tracks.exists())
        self.assertIsNone(self.borrower_content.default_subtitle_track)
        self.assertTrue(Subtitle.objects.filter(pk=self.track.pk).exists())
        self.assertIn(self.track, self.owner_content.subtitle_tracks.all())

    # --- creating and copying -----------------------------------------------------

    def test_a_new_track_is_added_to_the_content_it_was_created_from(self):
        response = self.post(
            self.borrower,
            "create_subtitle_track",
            self.borrower_content.pk,
            data={
                "create_mode": "empty",
                "name": "Mine",
                "language": LanguageFactory().pk,
            },
        )

        self.assertEqual(response.status_code, 201)
        created = Subtitle.objects.get(pk=response.json()["id"])
        self.assertEqual(list(self.borrower_content.subtitle_tracks.all()), [created])

    def test_copying_a_borrowed_track_replaces_it_and_keeps_it_the_default(self):
        self.borrower_content.subtitle_tracks.add(self.track)
        self.borrower_content.default_subtitle_track = self.track
        self.borrower_content.save()

        response = self.post(
            self.borrower,
            "copy_subtitle_track",
            self.borrower_content.pk,
            self.track.pk,
        )

        self.assertEqual(response.status_code, 201)
        copy = Subtitle.objects.get(pk=response.json()["id"])
        self.assertEqual(copy.owner, self.borrower)
        self.borrower_content.refresh_from_db()
        self.assertEqual(list(self.borrower_content.subtitle_tracks.all()), [copy])
        self.assertEqual(self.borrower_content.default_subtitle_track, copy)
        self.assertIn(self.track, self.owner_content.subtitle_tracks.all())

    def test_copying_from_the_create_dialog_adds_a_variant_alongside_the_original(self):
        response = self.post(
            self.owner,
            "create_subtitle_track",
            self.owner_content.pk,
            data={
                "create_mode": "copy",
                "source_id": self.track.pk,
                "name": "Ada's English (simplified)",
            },
        )

        self.assertEqual(response.status_code, 201)
        copy = Subtitle.objects.get(pk=response.json()["id"])
        self.assertCountEqual(
            self.owner_content.subtitle_tracks.all(), [self.track, copy]
        )

    # --- deleting -----------------------------------------------------------------

    def delete(self, user, data=None):
        login(self.client, user)
        return self.client.delete(
            reverse("delete_subtitle", args=[self.track.pk]),
            data=json.dumps(data or {}),
            content_type="application/json",
        )

    def test_deleting_a_track_no_other_video_uses_removes_it(self):
        response = self.delete(self.owner)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Subtitle.objects.filter(pk=self.track.pk).exists())
        self.assertFalse(self.owner_content.subtitle_tracks.exists())

    def test_deleting_a_track_another_video_uses_transfers_it_to_that_videos_owner(
        self,
    ):
        self.borrower_content.subtitle_tracks.add(self.track)

        response = self.delete(self.owner)

        self.assertEqual(response.status_code, 200)
        self.track.refresh_from_db()
        self.assertEqual(self.track.owner, self.borrower)
        self.assertFalse(self.owner_content.subtitle_tracks.exists())
        self.assertIn(self.track, self.borrower_content.subtitle_tracks.all())

    def test_the_delete_dialog_names_who_will_become_the_owner(self):
        self.borrower_content.subtitle_tracks.add(self.track)
        login(self.client, self.owner)

        response = self.client.get(
            reverse("delete_subtitle_track_dialog", args=[self.track.pk])
        )

        self.assertContains(response, self.borrower.username)
        self.assertContains(response, "Transfer and remove from my videos")

    def test_a_collaborator_can_edit_but_not_delete_the_owners_track(self):
        collaborator = UserFactory(instructor=True)
        PlaylistUserAccessFactory(
            playlist=self.owner_content.playlist,
            user=collaborator,
            playlist_role=PlaylistRole.COLLABORATOR,
        )

        response = self.delete(collaborator)

        self.assertEqual(response.status_code, 403)
        self.track.refresh_from_db()
        self.assertEqual(self.track.owner, self.owner)
        self.assertTrue(self.track.can_be_edited_by(collaborator))

    def test_deleting_the_owners_account_leaves_their_tracks_read_only_where_used(self):
        self.borrower_content.subtitle_tracks.add(self.track)

        self.owner.delete()

        self.track.refresh_from_db()
        self.assertIsNone(self.track.owner)
        self.assertIn(self.track, self.borrower_content.subtitle_tracks.all())
        self.assertTrue(self.track.can_be_read_by(self.borrower))
        self.assertFalse(self.track.can_be_edited_by(self.borrower))

    # --- what the editor offers -----------------------------------------------------

    def test_the_editor_lists_borrowed_tracks_as_read_only_with_their_owner(self):
        self.borrower_content.subtitle_tracks.add(self.track)

        context = self.editor_choices(self.borrower, self.borrower_content)

        [choice] = context["attached_subtitle_tracks"]
        self.assertEqual(choice["subtitle"], self.track)
        self.assertFalse(choice["can_edit"])
        self.assertFalse(choice["can_delete"])
        self.assertIn(
            "Ada Lovelace — read-only",
            render_to_string(
                "core/partials/subtitle_track_option_label.html", {"choice": choice}
            ),
        )

    def test_the_editor_offers_tracks_not_yet_on_the_video(self):
        context = self.editor_choices(self.borrower, self.borrower_content)

        self.assertEqual(context["attached_subtitle_tracks"], [])
        [choice] = context["unattached_subtitle_tracks"]
        self.assertEqual(choice["subtitle"], self.track)

    def test_choosing_a_default_track_in_admin_adds_it_to_the_video(self):
        login(self.client, UserFactory(is_superuser=True, is_staff=True))

        response = self.client.post(
            reverse("admin:core_content_change", args=[self.borrower_content.pk]),
            {
                "title": self.borrower_content.title,
                "playlist": self.borrower_content.playlist.pk,
                "resource_file": self.borrower_content.resource_file.pk,
                "default_subtitle_track": self.track.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(self.borrower_content.subtitle_tracks.all()), [self.track]
        )

    def test_admin_rejects_a_track_from_a_different_resource(self):
        other_track = SubtitleFactory(resource=ResourceFactory(), owner=self.owner)
        login(self.client, UserFactory(is_superuser=True, is_staff=True))

        response = self.client.post(
            reverse("admin:core_content_change", args=[self.borrower_content.pk]),
            {
                "title": self.borrower_content.title,
                "playlist": self.borrower_content.playlist.pk,
                "resource_file": self.borrower_content.resource_file.pk,
                "subtitle_tracks": [other_track.pk],
            },
        )

        self.assertContains(
            response, "Subtitle tracks must belong to this content&#x27;s resource."
        )
        self.assertFalse(self.borrower_content.subtitle_tracks.exists())

    def test_an_ownerless_track_can_still_be_opened_in_the_editor(self):
        Subtitle.objects.filter(pk=self.track.pk).update(owner=None)
        login(self.client, self.owner)

        response = self.client.get(
            reverse("edit_subtitle_track", args=[self.track.pk]),
            {"content_id": self.owner_content.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Owner: no owner")
