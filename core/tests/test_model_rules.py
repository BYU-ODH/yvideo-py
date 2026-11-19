"""Rules the models enforce for every writer, not only the forms and views (#554)."""

from datetime import timedelta
import importlib
import json
from pathlib import Path
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test import modify_settings
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from ..factories import AnnotationSetFactory
from ..factories import ContentFactory
from ..factories import PlaylistFactory
from ..factories import ResourceAccessFactory
from ..factories import ResourceFactory
from ..factories import ResourceFileFactory
from ..factories import SubtitleFactory
from ..factories import TrackFactory
from ..factories import UserFactory
from ..models import Language
from ..models import PauseAnnotation
from ..models import ResourceIntakeRequest
from ..models import SkipAnnotation

MIGRATION = importlib.import_module("core.migrations.0028_model_rules")


class EditorRuleTests(TestCase):
    def setUp(self):
        self.owner = UserFactory(instructor=True)
        self.content = ContentFactory(playlist=PlaylistFactory(owner=self.owner))
        self.resource = self.content.get_resource()
        self.annotation_set = AnnotationSetFactory(
            owner=self.owner, resource=self.resource
        )
        self.content.annotation_set = self.annotation_set
        self.content.save()
        self.track = TrackFactory(annotation_set=self.annotation_set)
        self.client.force_login(self.owner)

    def post_json(self, url, payload):
        return self.client.post(
            url, data=json.dumps(payload), content_type="application/json"
        )

    def test_an_annotation_set_for_another_resource_cannot_be_selected(self):
        other_set = AnnotationSetFactory(owner=self.owner)

        response = self.post_json(
            reverse("select_annotation_set", args=[self.content.pk]),
            {"annotation_set_id": other_set.pk},
        )

        self.assertEqual(response.status_code, 400)
        self.content.refresh_from_db()
        self.assertEqual(self.content.annotation_set, self.annotation_set)

    def test_moving_content_to_another_resources_file_keeps_its_set_in_check(self):
        self.content.resource_file = ResourceFileFactory()

        with self.assertRaises(ValidationError) as raised:
            self.content.full_clean()

        self.assertIn("annotation_set", raised.exception.message_dict)

    def test_an_annotation_cannot_end_before_it_starts(self):
        response = self.post_json(
            reverse("create_annotation", args=[self.track.pk, "skip"]),
            {"start_time": 10, "end_time": 5},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(SkipAnnotation.objects.exists())

    def test_a_pause_can_be_added_and_moved_after_the_start(self):
        response = self.post_json(
            reverse("create_annotation", args=[self.track.pk, "pause"]),
            {"start_time": 5},
        )
        self.assertEqual(response.status_code, 200)
        pause = PauseAnnotation.objects.get()

        response = self.post_json(
            reverse("update_annotation", args=[self.content.pk, "pause", pause.pk]),
            {"start_time": "0:00:08"},
        )

        self.assertEqual(response.status_code, 200)
        moved = PauseAnnotation.objects.get(active=True)
        self.assertEqual((moved.start_time, moved.end_time), (8.0, 8.0))


class ContentAdminTests(TestCase):
    def setUp(self):
        self.playlist = PlaylistFactory()
        self.client.force_login(UserFactory(is_superuser=True, is_staff=True))

    def add_content(self, resource_file):
        return self.client.post(
            reverse("admin:core_content_add"),
            {
                "title": "Clip",
                "playlist": self.playlist.pk,
                "resource_file": resource_file.pk,
            },
        )

    def test_content_can_use_a_file_the_playlist_owner_can_access(self):
        resource_file = ResourceFileFactory()
        ResourceAccessFactory(user=self.playlist.owner, resource=resource_file.resource)

        response = self.add_content(resource_file)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.playlist.contents.filter(title="Clip").exists())

    def test_content_cannot_use_a_file_the_playlist_owner_cannot_access(self):
        response = self.add_content(ResourceFileFactory())

        self.assertContains(
            response, "The playlist owner doesn&#x27;t have access to this file&#x27;s"
        )
        self.assertFalse(self.playlist.contents.exists())

    def test_editing_content_offers_library_files_without_a_direct_grant(self):
        content = ContentFactory(playlist=self.playlist)
        library_file = ResourceFileFactory(
            resource=ResourceFactory(
                checked_out_from_hbll=True, byu_call_number="PN1997 .A1"
            )
        )

        response = self.client.post(
            reverse("admin:core_content_change", args=[content.pk]),
            {
                "title": content.title,
                "playlist": self.playlist.pk,
                "resource_file": library_file.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        content.refresh_from_db()
        self.assertEqual(content.resource_file, library_file)


class StoredFileTests(TestCase):
    def setUp(self):
        media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(media_directory.cleanup)
        self.media_root = Path(media_directory.name)
        media_override = override_settings(MEDIA_ROOT=self.media_root)
        media_override.enable()
        self.addCleanup(media_override.disable)

    def test_uploads_are_stored_under_the_resource_id(self):
        resource = ResourceFactory(name="AC/DC: Live")
        resource_file = ResourceFileFactory(resource=resource, version="")
        subtitle = SubtitleFactory(resource=resource)

        self.assertTrue(resource_file.file.name.startswith(f"{resource.pk}/"))
        self.assertTrue(
            subtitle.subtitles_file.name.startswith(f"{resource.pk}/subtitles/")
        )

    def test_deleting_a_resource_removes_its_media_and_subtitle_files(self):
        resource = ResourceFactory()
        paths = [
            Path(ResourceFileFactory(resource=resource).file.path),
            Path(SubtitleFactory(resource=resource).subtitles_file.path),
        ]

        with self.captureOnCommitCallbacks(execute=True):
            resource.delete()

        for path in paths:
            self.assertFalse(path.exists(), path)

    def test_replacing_a_file_updates_its_checksum_and_removes_the_old_bytes(self):
        resource_file = ResourceFileFactory()
        original_checksum = resource_file.checksum
        original_path = Path(resource_file.file.path)

        resource_file.file = SimpleUploadedFile("new.mp4", b"different bytes")
        with self.captureOnCommitCallbacks(execute=True):
            resource_file.save()

        self.assertNotEqual(resource_file.checksum, original_checksum)
        self.assertFalse(original_path.exists())
        self.assertTrue(Path(resource_file.file.path).exists())


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
@override_settings(DEBUG=True)
class IntakeRequestTests(TestCase):
    def setUp(self):
        self.english = Language.objects.get(bcp47="en")
        self.client.force_login(UserFactory(instructor=True))

    def submit(self, **overrides):
        data = {
            "resource_title": "Amélie",
            "date_needed": (timezone.localdate() + timedelta(days=7)).isoformat(),
            "audio_language": self.english.pk,
            "acknowledged_compliance": "on",
            "acknowledged_fair_use_limitation": "on",
        }
        data.update(overrides)
        return self.client.post(reverse("request_resource"), data)

    def test_a_request_records_the_chosen_audio_language(self):
        self.submit()

        self.assertEqual(
            ResourceIntakeRequest.objects.get().audio_language, self.english
        )

    def test_a_language_not_in_the_list_can_be_described(self):
        self.submit(audio_language="other", language_notes="Old Norse")

        intake_request = ResourceIntakeRequest.objects.get()
        self.assertIsNone(intake_request.audio_language)
        self.assertEqual(intake_request.language_notes, "Old Norse")

    def test_choosing_other_needs_a_description(self):
        self.submit(subtitle_language="other")

        self.assertFalse(ResourceIntakeRequest.objects.exists())

    def test_a_request_needs_an_audio_language(self):
        self.submit(audio_language="")

        self.assertFalse(ResourceIntakeRequest.objects.exists())

    def test_a_library_copy_can_be_requested_before_staff_add_its_call_number(self):
        self.submit(checked_out_from_hbll="on")

        self.assertTrue(ResourceIntakeRequest.objects.exists())

    def test_free_text_languages_are_matched_when_migrating(self):
        spanish = Language.objects.get(bcp47="es")
        cases = {"English": self.english, "spanish (Spain)": spanish, "Klingon": None}
        resolved = {
            text: MIGRATION.resolve_language(text, Language.objects.all())
            for text in cases
        }

        self.assertEqual(resolved, cases)
