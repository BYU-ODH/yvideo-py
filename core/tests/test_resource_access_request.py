"""Two-step resource request: search the library, then request access or intake (#508)."""

from django.test import TestCase
from django.test import modify_settings
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from reversion.models import Version

from ..factories import ResourceFactory
from ..factories import ResourceFileFactory
from ..factories import UserFactory
from ..models import Resource
from ..models import ResourceAccessRequest


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
@override_settings(DEBUG=True)
class ResourceAccessRequestTests(TestCase):
    def setUp(self):
        self.url = reverse("request_resource")
        self.instructor = UserFactory(instructor=True, netid="rar_instr")
        self.resource = ResourceFactory(
            name="Amelie", name_disambiguator="2001", imdb_id="tt0211915"
        )
        self.client.force_login(self.instructor)

    def request_access(self, resource):
        return self.client.post(reverse("request_resource_access", args=[resource.pk]))

    def test_the_intake_form_appears_only_after_searching(self):
        response = self.client.get(self.url)

        self.assertNotContains(response, 'id="resource-intake-form"')

        response = self.client.get(self.url, {"q": "Nonexistent film"})

        self.assertContains(response, 'id="resource-intake-form"')
        self.assertContains(response, 'value="Nonexistent film"')

    def test_search_by_title_shows_imdb_link_and_barcode(self):
        ResourceFileFactory(resource=self.resource, barcode="012345678905")

        response = self.client.get(self.url, {"q": "amel"})

        self.assertContains(response, "Amelie (2001)")
        self.assertContains(response, "https://www.imdb.com/title/tt0211915/")
        self.assertContains(response, "012345678905")
        self.assertContains(response, "Request access")

    def test_search_by_barcode(self):
        ResourceFileFactory(resource=self.resource, barcode="012345678905")

        response = self.client.get(self.url, {"q": "012345678905"})

        self.assertContains(response, "Amelie (2001)")

    def test_youtube_resources_are_not_listed(self):
        ResourceFactory(name="Amelie trailer", media_type=Resource.MediaType.WEB)

        response = self.client.get(self.url, {"q": "amelie"})

        self.assertNotContains(response, "Amelie trailer")

    def test_requesting_access_is_pending_until_staff_grant_it(self):
        response = self.request_access(self.resource)

        self.assertRedirects(response, f"{self.url}?q=Amelie")
        self.assertFalse(self.instructor.can_access_resource(self.resource))
        response = self.client.get(self.url, {"q": "Amelie"})
        self.assertContains(response, "Access requested")
        self.assertNotContains(response, "Request access")

        lab_assistant = UserFactory(lab_assistant=True, netid="rar_la")
        self.client.force_login(lab_assistant)
        response = self.client.get(reverse("resource_access_requests"))
        self.assertContains(response, "Amelie (2001)")
        self.client.post(
            reverse(
                "approve_resource_access_request",
                args=[ResourceAccessRequest.objects.get().pk],
            )
        )

        self.assertTrue(self.instructor.can_access_resource(self.resource))
        approval = Version.objects.get_for_object(ResourceAccessRequest.objects.get())
        self.assertEqual(approval.first().revision.user, lab_assistant)
        self.client.force_login(self.instructor)
        response = self.client.get(self.url, {"q": "Amelie"})
        self.assertContains(response, "You already have access")

    def test_a_student_cannot_request_access(self):
        self.client.force_login(UserFactory(student=True, netid="rar_stud"))

        response = self.request_access(self.resource)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ResourceAccessRequest.objects.exists())

    def test_access_can_be_requested_again_after_it_is_revoked(self):
        ResourceAccessRequest.objects.create(
            user=self.instructor, resource=self.resource, approved_at=timezone.now()
        )

        self.request_access(self.resource)

        response = self.client.get(self.url, {"q": "Amelie"})
        self.assertContains(response, "Access requested")

    def test_an_instructor_cannot_approve_their_own_request(self):
        self.request_access(self.resource)

        response = self.client.post(
            reverse(
                "approve_resource_access_request",
                args=[ResourceAccessRequest.objects.get().pk],
            )
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.instructor.can_access_resource(self.resource))
