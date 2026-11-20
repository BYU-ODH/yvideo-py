"""Two-step resource request: search the library, then request access or intake (#508)."""

import re

from django.contrib.messages import get_messages
from django.test import TestCase
from django.test import modify_settings
from django.test import override_settings
from django.urls import reverse
from reversion.models import Version

from ..factories import ResourceFactory
from ..factories import ResourceFileFactory
from ..factories import UserFactory
from ..models import Resource
from ..models import ResourceAccess
from ..models import ResourceAccessRequest
from ..models import ResourceIntakeRequest


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

    def request_access(self, resource, **compliance):
        data = {
            "acknowledged_compliance": "on",
            "acknowledged_fair_use_limitation": "on",
        }
        return self.client.post(
            reverse("request_resource_access", args=[resource.pk]),
            data | compliance,
        )

    def assertIntakeFormOpen(self, response, is_open):
        tag = re.search(
            r'<details id="resource-intake-details"[^>]*>', response.content.decode()
        )
        self.assertEqual(bool(re.search(r"\sopen\b", tag[0])), is_open)

    def test_the_intake_form_appears_only_after_searching(self):
        response = self.client.get(self.url)

        self.assertNotContains(response, 'id="resource-intake-form"')

        response = self.client.get(self.url, {"q": "Nonexistent film"})

        self.assertContains(response, 'id="resource-intake-form"')
        self.assertContains(response, 'value="Nonexistent film"')

    def test_intake_form_starts_collapsed_only_when_there_are_results(self):
        self.assertIntakeFormOpen(self.client.get(self.url, {"q": "Amelie"}), False)
        self.assertIntakeFormOpen(
            self.client.get(self.url, {"q": "Nonexistent film"}), True
        )

    def test_a_rejected_intake_request_reopens_the_form(self):
        response = self.client.post(f"{self.url}?q=Amelie", {})

        self.assertIntakeFormOpen(response, True)

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

    def test_requesting_access_keeps_the_search(self):
        response = self.request_access(self.resource, q="ame")

        self.assertRedirects(response, f"{self.url}?q=ame")

    def test_search_results_ask_for_legal_compliance_before_requesting(self):
        response = self.client.get(self.url, {"q": "Amelie"})

        self.assertContains(response, "first agree to the following")

    def test_requesting_access_is_pending_until_staff_grant_it(self):
        response = self.request_access(self.resource)

        self.assertRedirects(response, f"{self.url}?q=Amelie")
        self.assertFalse(self.instructor.can_access_resource(self.resource))
        response = self.client.get(self.url, {"q": "Amelie"})
        self.assertContains(response, "Access requested")
        self.assertNotContains(response, "Request access")
        self.assertNotContains(response, "first agree to the following")

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
        response = self.client.get(reverse("resource_access_requests"))
        self.assertContains(response, "No pending access requests.")
        grant = ResourceAccess.objects.get(user=self.instructor, resource=self.resource)
        approval = Version.objects.get_for_object(grant)
        self.assertEqual(approval.first().revision.user, lab_assistant)
        agreement = Version.objects.get_for_model(ResourceAccessRequest).get()
        self.assertEqual(agreement.revision.user, self.instructor)
        self.assertTrue(agreement.field_dict["acknowledged_compliance"])
        self.assertTrue(agreement.field_dict["acknowledged_fair_use_limitation"])
        self.client.force_login(self.instructor)
        response = self.client.get(self.url, {"q": "Amelie"})
        self.assertContains(response, "You already have access")

    def test_requesting_access_requires_agreeing_to_legal_compliance(self):
        response = self.request_access(
            self.resource, acknowledged_fair_use_limitation=""
        )

        self.assertRedirects(response, f"{self.url}?q=Amelie")
        self.assertIn(
            "Please agree to the legal compliance terms",
            str(next(iter(get_messages(response.wsgi_request)))),
        )
        self.assertFalse(ResourceAccessRequest.objects.exists())

    def test_a_student_cannot_request_access(self):
        self.client.force_login(UserFactory(student=True, netid="rar_stud"))

        response = self.request_access(self.resource)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ResourceAccessRequest.objects.exists())

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

    def test_staff_see_pending_new_resource_requests(self):
        acknowledged = {
            "acknowledged_compliance": True,
            "acknowledged_fair_use_limitation": True,
        }
        ResourceIntakeRequest.objects.create(
            owner=self.instructor, resource_title="Delicatessen", **acknowledged
        )
        ResourceIntakeRequest.objects.create(
            owner=self.instructor,
            resource_title="Already processed",
            generated_resource=self.resource,
            **acknowledged,
        )
        self.client.force_login(UserFactory(lab_assistant=True, netid="rar_la"))

        response = self.client.get(reverse("resource_access_requests"))

        self.assertContains(response, "Delicatessen")
        self.assertNotContains(response, "Already processed")
        self.assertNotContains(response, "Open in admin")

        self.client.force_login(UserFactory(admin=True, netid="rar_admin"))
        response = self.client.get(reverse("resource_access_requests"))

        self.assertContains(response, "Open in admin")
