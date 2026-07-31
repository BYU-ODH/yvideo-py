from django.test import TestCase
from django.test import modify_settings
from django.test import override_settings
from django.urls import reverse

from ..factories import UserFactory


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
@override_settings(DEBUG=True)
class SpoofingTests(TestCase):
    def setUp(self):
        self.admin = UserFactory(admin=True, netid="admin1")
        self.other_admin = UserFactory(admin=True, netid="admin2")
        self.lab_assistant = UserFactory(lab_assistant=True, netid="labasst")
        self.student = UserFactory(student=True, netid="student1")

    def _start(self, target):
        return self.client.post(reverse("start_spoofing"), {"spoof_user_id": target.pk})

    def test_admin_can_spoof_any_user(self):
        self.client.force_login(self.admin)
        self._start(self.student)

        response = self.client.get("/")

        self.assertEqual(response.wsgi_request.user, self.student)
        self.assertTrue(response.wsgi_request.is_spoofing)

    def test_admin_can_spoof_another_admin(self):
        self.client.force_login(self.admin)
        self._start(self.other_admin)

        response = self.client.get("/")

        self.assertEqual(response.wsgi_request.user, self.other_admin)
        self.assertTrue(response.wsgi_request.is_spoofing)

    def test_lab_assistant_can_spoof_non_admin_user(self):
        self.client.force_login(self.lab_assistant)
        self._start(self.student)

        response = self.client.get("/")

        self.assertEqual(response.wsgi_request.user, self.student)
        self.assertTrue(response.wsgi_request.is_spoofing)

    def test_lab_assistant_cannot_spoof_admin(self):
        self.client.force_login(self.lab_assistant)
        self._start(self.admin)

        response = self.client.get("/")

        self.assertEqual(response.wsgi_request.user, self.lab_assistant)
        self.assertFalse(response.wsgi_request.is_spoofing)
        self.assertNotIn("spoof_user_id", self.client.session)

    def test_student_cannot_start_spoofing(self):
        self.client.force_login(self.student)

        response = self._start(self.other_admin)

        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn("spoof_user_id", self.client.session)

    def test_lab_assistant_spoof_denial_is_logged_with_actor_and_target(self):
        self.client.force_login(self.lab_assistant)

        with self.assertLogs("core.views", level="WARNING") as captured:
            self._start(self.admin)

        [message] = captured.output
        self.assertIn("SPOOF DENIED", message)
        self.assertIn(self.lab_assistant.netid, message)
        self.assertIn(self.admin.netid, message)

    def test_active_lab_assistant_spoof_is_logged_with_role_actor_and_target(self):
        self.client.force_login(self.lab_assistant)
        self._start(self.student)

        with self.assertLogs("core.middleware", level="INFO") as captured:
            self.client.get("/")

        [message] = captured.output
        self.assertIn("SPOOF ACTIVE", message)
        self.assertIn("Lab Assistant", message)
        self.assertIn(self.lab_assistant.netid, message)
        self.assertIn(self.student.netid, message)

    def test_active_admin_spoof_is_logged_with_admin_role(self):
        self.client.force_login(self.admin)
        self._start(self.student)

        with self.assertLogs("core.middleware", level="INFO") as captured:
            self.client.get("/")

        [message] = captured.output
        self.assertIn("Admin", message)
        self.assertNotIn("Lab Assistant", message)

    def test_lab_assistant_spoof_search_excludes_admins(self):
        self.client.force_login(self.lab_assistant)

        response = self.client.post(reverse("spoof_user_search"), {"search": ""})

        self.assertNotContains(response, self.admin.netid)
        self.assertNotContains(response, self.other_admin.netid)
        self.assertContains(response, self.student.netid)

    def test_admin_spoof_search_includes_admins(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("spoof_user_search"), {"search": ""})

        self.assertContains(response, self.other_admin.netid)
        self.assertContains(response, self.student.netid)

    def test_revoking_lab_assistant_role_mid_spoof_deactivates_it(self):
        self.client.force_login(self.lab_assistant)
        self._start(self.student)

        self.lab_assistant.groups.clear()
        response = self.client.get("/")

        self.assertEqual(response.wsgi_request.user, self.lab_assistant)
        self.assertFalse(response.wsgi_request.is_spoofing)
