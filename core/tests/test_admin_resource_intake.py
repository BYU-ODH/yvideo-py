from datetime import timedelta
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test import modify_settings
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Language
from core.models import Resource
from core.models import ResourceAccess
from core.models import ResourceFile
from core.models import ResourceIntakeRequest
from core.models import User

UPLOAD_FIELDS = (
    "new_resource_file",
    "new_resource_file_version",
    "new_resource_file_audio_language",
    "new_resource_file_subtitle_language",
    "new_resource_file_full_video",
    "new_resource_file_barcode",
)


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
@override_settings(DEBUG=True)
class ResourceIntakeRequestAdminTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media_directory.cleanup)

        self.admin_user = User.objects.create_superuser(
            username="admin", password="password"
        )
        self.owner = User.objects.create(
            username="123456789",
            netid="requester",
            first_name="Requesting",
            last_name="Instructor",
        )
        self.client.force_login(self.admin_user)
        self.english = Language.objects.create(language="English", lang_tag="en")
        self.spanish = Language.objects.create(language="Spanish", lang_tag="es")
        self.french = Language.objects.create(language="French", lang_tag="fr")

    def create_request(self, **overrides):
        values = {
            "owner": self.owner,
            "date_needed": timezone.localdate() + timedelta(days=7),
            "resource_title": "The Grand Budapest Hotel",
            "imdb_link": "https://www.imdb.com/title/tt2278388/",
            "audio_language": "English",
            "subtitle_language": "Spanish",
            "acknowledged_compliance": True,
            "acknowledged_fair_use_limitation": True,
        }
        values.update(overrides)
        return ResourceIntakeRequest.objects.create(**values)

    def create_resource(self, **overrides):
        values = {
            "name": "The Grand Budapest Hotel",
            "media_type": Resource.MediaType.VIDEO,
            "requester_username": "someone",
            "imdb_id": "tt2278388",
        }
        values.update(overrides)
        return Resource.objects.create(**values)

    def create_resource_file(self, resource, checksum, **overrides):
        values = {
            "resource": resource,
            "file": f"existing/{checksum}.mp4",
            "version": checksum,
            "audio_language": self.english,
            "burned_in_subtitles_language": self.spanish,
            "checksum": checksum,
        }
        values.update(overrides)
        return ResourceFile.objects.create(**values)

    @staticmethod
    def upload(name, content=None, content_type="video/mp4"):
        return SimpleUploadedFile(
            name,
            content if content is not None else name.encode(),
            content_type=content_type,
        )

    def change_payload(self, intake_request, *, omit=(), **overrides):
        imdb_id = next(
            (
                part
                for part in intake_request.imdb_link.split("/")
                if part.startswith("tt")
            ),
            "",
        )
        values = {
            "owner": intake_request.owner_id,
            "date_needed": intake_request.date_needed.isoformat(),
            "resource_title": intake_request.resource_title,
            "imdb_link": intake_request.imdb_link,
            "audio_language": intake_request.audio_language,
            "subtitle_language": intake_request.subtitle_language,
            "acknowledged_compliance": "on",
            "acknowledged_fair_use_limitation": "on",
            "new_resource_imdb_id": imdb_id,
            "new_resource_file_full_video": "on",
            "new_resource_file_has_no_barcode": "on",
            "user_has_shown_proof_of_ownership": "on",
            "_approve_request": "Approve request",
        }
        values.update(overrides)
        for field_name in omit:
            values.pop(field_name, None)
        return values

    def change_url(self, intake_request):
        return reverse(
            "admin:core_resourceintakerequest_change",
            args=(intake_request.pk,),
        )

    def test_change_form_lists_resource_and_file_decisions(self):
        intake_request = self.create_request()
        imdb_match = self.create_resource()
        matching_file = self.create_resource_file(imdb_match, "matching")
        self.create_resource_file(
            imdb_match,
            "wronglang",
            burned_in_subtitles_language=self.french,
        )
        fuzzy_match = self.create_resource(
            name="Grand Budapest Hotel",
            imdb_id="tt9999999",
        )

        response = self.client.get(self.change_url(intake_request))

        self.assertTemplateUsed(
            response,
            "admin/core/resourceintakerequest/change_form.html",
        )
        self.assertContains(response, "Intake Decisions")
        self.assertNotContains(response, 'name="date_needed_1"')
        self.assertContains(response, "Create from scratch")
        self.assertContains(response, imdb_match.name)
        self.assertContains(response, "https://www.imdb.com/title/tt2278388/")
        self.assertContains(response, fuzzy_match.name)
        self.assertContains(response, "Upload new file")
        self.assertContains(response, matching_file.version)
        self.assertContains(response, "Audio:</strong> English (en)")
        self.assertContains(response, "Burned-in subtitles:</strong> Spanish (es)")
        self.assertContains(response, "Matches requested languages")
        self.assertContains(response, "Does not match requested languages")
        form = response.context["adminform"].form
        self.assertEqual(len(form.fields["resource_to_use"].choices), 3)
        self.assertEqual(len(form.fields["file_to_use"].choices), 3)
        self.assertContains(response, "Approve request")
        self.assertContains(response, 'name="_save"')
        for field_name in UPLOAD_FIELDS:
            self.assertFalse(form.fields[field_name].required)

    def test_approve_requires_proof_of_ownership(self):
        intake_request = self.create_request()
        resource = self.create_resource()
        resource_file = self.create_resource_file(resource, "matching")
        payload = self.change_payload(
            intake_request,
            omit=("user_has_shown_proof_of_ownership",),
            resource_to_use=str(resource.pk),
            file_to_use=str(resource_file.pk),
        )

        response = self.client.post(
            self.change_url(intake_request),
            payload,
        )

        self.assertContains(
            response,
            "Confirm that the user has shown proof of ownership",
        )
        self.assertFalse(ResourceAccess.objects.filter(user=self.owner).exists())

    def test_resource_without_imdb_id_uses_plain_missing_id_label(self):
        intake_request = self.create_request(
            resource_title="Movie Without IMDb",
            imdb_link="",
        )
        self.create_resource(
            name="Movie Without IMDb",
            imdb_id="",
        )

        response = self.client.get(self.change_url(intake_request))

        self.assertContains(response, "(no IMDb id)")
        self.assertNotContains(response, "Search IMDb")

    def test_new_resource_forces_upload_choice(self):
        intake_request = self.create_request()
        resource = self.create_resource()
        resource_file = self.create_resource_file(resource, "matching")
        payload = self.change_payload(
            intake_request,
            omit=("_approve_request",),
            resource_to_use="create-resource",
            file_to_use=str(resource_file.pk),
            _save="Save",
        )

        response = self.client.post(
            self.change_url(intake_request),
            payload,
        )

        form = response.context["adminform"].form
        self.assertEqual(form.cleaned_data["file_to_use"], "upload-file")
        self.assertEqual(list(form.errors), ["new_resource_file"])
        self.assertEqual(Resource.objects.count(), 1)

    def test_only_file_and_barcode_decision_are_required_for_an_upload(self):
        intake_request = self.create_request(
            resource_title="No Existing Resource",
            imdb_link="https://www.imdb.com/title/tt7654321/",
        )

        response = self.client.get(self.change_url(intake_request))

        form = response.context["adminform"].form
        self.assertEqual(form.fields["file_to_use"].initial, "upload-file")
        required_fields = {name for name in UPLOAD_FIELDS if form.fields[name].required}
        self.assertEqual(
            required_fields,
            {"new_resource_file", "new_resource_file_barcode"},
        )

    def test_approve_existing_file_associates_request_and_grants_access(self):
        intake_request = self.create_request()
        resource = self.create_resource()
        resource_file = self.create_resource_file(resource, "matching")

        response = self.client.post(
            self.change_url(intake_request),
            self.change_payload(
                intake_request,
                resource_to_use=str(resource.pk),
                file_to_use=str(resource_file.pk),
            ),
            follow=True,
        )

        self.assertTrue(
            ResourceAccess.objects.filter(user=self.owner, resource=resource).exists()
        )
        intake_request.refresh_from_db()
        self.assertEqual(intake_request.generated_resource, resource)
        self.assertContains(response, "Approved the request")

    def test_existing_file_must_belong_to_selected_resource(self):
        intake_request = self.create_request()
        selected_resource = self.create_resource()
        other_resource = self.create_resource(
            name="Grand Budapest Hotel",
            imdb_id="tt9999999",
        )
        other_file = self.create_resource_file(other_resource, "other")

        response = self.client.post(
            self.change_url(intake_request),
            self.change_payload(
                intake_request,
                resource_to_use=str(selected_resource.pk),
                file_to_use=str(other_file.pk),
            ),
        )

        self.assertContains(response, "does not belong to the selected Resource")
        self.assertFalse(ResourceAccess.objects.filter(user=self.owner).exists())

    def test_existing_file_with_language_mismatch_requires_an_upload(self):
        intake_request = self.create_request()
        resource = self.create_resource()
        resource_file = self.create_resource_file(
            resource,
            "wronglang",
            burned_in_subtitles_language=self.french,
        )

        response = self.client.post(
            self.change_url(intake_request),
            self.change_payload(
                intake_request,
                resource_to_use=str(resource.pk),
                file_to_use=str(resource_file.pk),
            ),
        )

        self.assertContains(response, "does not match the requested audio")
        self.assertFalse(ResourceAccess.objects.filter(user=self.owner).exists())

    def test_approve_upload_creates_validated_file_and_grants_access(self):
        intake_request = self.create_request()
        resource = self.create_resource()

        self.client.post(
            self.change_url(intake_request),
            self.change_payload(
                intake_request,
                resource_to_use=str(resource.pk),
                file_to_use="upload-file",
                new_resource_file=self.upload("requested-version.mp4"),
                new_resource_file_version="Spanish subtitle edition",
                new_resource_file_audio_language=self.english.pk,
                new_resource_file_subtitle_language=self.spanish.pk,
            ),
            follow=True,
        )

        resource_file = ResourceFile.objects.get()
        self.assertEqual(resource_file.resource, resource)
        self.assertEqual(resource_file.version, "Spanish subtitle edition")
        self.assertEqual(resource_file.audio_language, self.english)
        self.assertEqual(resource_file.burned_in_subtitles_language, self.spanish)
        self.assertTrue(resource_file.full_video)
        self.assertTrue(resource_file.barcode.startswith("BYU"))
        self.assertTrue(
            ResourceAccess.objects.filter(user=self.owner, resource=resource).exists()
        )
        intake_request.refresh_from_db()
        self.assertEqual(intake_request.generated_resource, resource)

    def test_upload_allows_clip_with_blank_optional_metadata(self):
        intake_request = self.create_request(
            audio_language="",
            subtitle_language="",
        )
        resource = self.create_resource()
        payload = self.change_payload(
            intake_request,
            omit=("new_resource_file_full_video",),
            resource_to_use=str(resource.pk),
            file_to_use="upload-file",
            new_resource_file=self.upload("silent-film.mp4"),
        )

        self.client.post(
            self.change_url(intake_request),
            payload,
            follow=True,
        )

        resource_file = ResourceFile.objects.get()
        self.assertEqual(resource_file.version, "")
        self.assertIsNone(resource_file.audio_language)
        self.assertIsNone(resource_file.burned_in_subtitles_language)
        self.assertFalse(resource_file.full_video)

    def test_upload_accepts_an_explicit_barcode(self):
        intake_request = self.create_request()
        resource = self.create_resource()
        payload = self.change_payload(
            intake_request,
            omit=("new_resource_file_has_no_barcode",),
            resource_to_use=str(resource.pk),
            file_to_use="upload-file",
            new_resource_file=self.upload("barcoded.mp4"),
            new_resource_file_barcode="012345678901",
        )

        self.client.post(
            self.change_url(intake_request),
            payload,
            follow=True,
        )

        self.assertEqual(ResourceFile.objects.get().barcode, "012345678901")

    def test_upload_requires_barcode_or_no_barcode_confirmation(self):
        intake_request = self.create_request()
        resource = self.create_resource()
        payload = self.change_payload(
            intake_request,
            omit=("new_resource_file_has_no_barcode",),
            resource_to_use=str(resource.pk),
            file_to_use="upload-file",
            new_resource_file=self.upload("missing-barcode.mp4"),
        )

        response = self.client.post(
            self.change_url(intake_request),
            payload,
        )

        self.assertContains(response, "This field is required.")
        self.assertFalse(ResourceFile.objects.exists())

    def test_upload_reuses_resourcefile_duplicate_content_validation(self):
        intake_request = self.create_request()
        resource = self.create_resource()
        duplicate_content = b"duplicate video content"
        existing_file = ResourceFile(
            resource=resource,
            file=self.upload("existing.mp4", duplicate_content),
            version="existing",
            audio_language=self.english,
            burned_in_subtitles_language=self.spanish,
        )
        existing_file.save()

        response = self.client.post(
            self.change_url(intake_request),
            self.change_payload(
                intake_request,
                resource_to_use=str(resource.pk),
                file_to_use="upload-file",
                new_resource_file=self.upload("duplicate.mp4", duplicate_content),
            ),
        )

        self.assertContains(response, "same content already exists")
        self.assertEqual(ResourceFile.objects.count(), 1)

    def test_upload_reuses_resourcefile_media_type_validation(self):
        intake_request = self.create_request()
        resource = self.create_resource()

        response = self.client.post(
            self.change_url(intake_request),
            self.change_payload(
                intake_request,
                resource_to_use=str(resource.pk),
                file_to_use="upload-file",
                new_resource_file=self.upload(
                    "not-media.exe",
                    content_type="application/octet-stream",
                ),
            ),
        )

        self.assertContains(response, "File type not supported")
        self.assertFalse(ResourceFile.objects.exists())

    def test_approve_creates_new_resource_and_file(self):
        intake_request = self.create_request(
            resource_title="A Brand New Resource",
            imdb_link="https://www.imdb.com/title/tt7654321/",
        )

        self.client.post(
            self.change_url(intake_request),
            self.change_payload(
                intake_request,
                resource_to_use="create-resource",
                file_to_use="upload-file",
                new_resource_file=self.upload("brand-new.mp4"),
            ),
            follow=True,
        )

        resource = Resource.objects.get()
        resource_file = ResourceFile.objects.get()
        self.assertEqual(resource.name, "A Brand New Resource")
        self.assertEqual(resource.imdb_id, "tt7654321")
        self.assertEqual(resource_file.resource, resource)

    def test_new_resource_requires_imdb_id_or_not_in_imdb_confirmation(self):
        intake_request = self.create_request(
            resource_title="Unidentified New Resource",
            imdb_link="",
        )

        response = self.client.post(
            self.change_url(intake_request),
            self.change_payload(
                intake_request,
                resource_to_use="create-resource",
                file_to_use="upload-file",
                new_resource_file=self.upload("unidentified.mp4"),
            ),
        )

        self.assertContains(response, "This field is required.")
        self.assertFalse(Resource.objects.exists())

    def test_new_resource_not_in_imdb_gets_generated_internal_imdb_id(self):
        intake_request = self.create_request(
            resource_title="Resource Not In IMDb",
            imdb_link="",
        )
        payload = self.change_payload(
            intake_request,
            resource_to_use="create-resource",
            file_to_use="upload-file",
            new_resource_not_in_imdb="on",
            new_resource_file=self.upload("not-in-imdb.mp4"),
        )

        self.client.post(
            self.change_url(intake_request),
            payload,
            follow=True,
        )

        resource = Resource.objects.get()
        self.assertRegex(resource.imdb_id, r"^BYU\d{10}$")
