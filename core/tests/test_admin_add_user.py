from unittest.mock import patch

from django.test import TestCase
from django.test import modify_settings
from django.urls import reverse

from core.models import Course
from core.models import PrivilegeLevel
from core.models import User
from core.models import UserCourses

ENROLLMENT_OK = {
    "is_current_sem_updated": True,
    "is_next_sem_updated": True,
    "result_message": "",
}


@modify_settings(
    MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
)
class AddUserAdminViewTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin-byuid", password="password"
        )
        self.client.force_login(self.admin_user)
        self.add_url = reverse("admin:core_user_add")

    def test_get_renders_lookup_form(self):
        response = self.client.get(self.add_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BYU ID or NetID")

    def test_existing_byu_id_redirects_to_existing_user_without_calling_api(self):
        existing = User.objects.create(username="111111111", netid="jdoe")

        with patch("yvideo.odhOIDCAuthenticationBackend.Api") as MockApi:
            response = self.client.post(self.add_url, {"identifier": "111111111"})

        MockApi.assert_not_called()
        self.assertRedirects(
            response, reverse("admin:core_user_change", args=(existing.pk,))
        )

    def test_new_byu_id_creates_and_populates_user_from_api(self):
        with (
            patch("yvideo.odhOIDCAuthenticationBackend.Api") as MockApi,
            patch(
                "core.forms.update_user_enrollment", return_value=ENROLLMENT_OK
            ) as mock_update_enrollment,
        ):
            api = MockApi.return_value
            api.get_worker_id_from_byu_id.return_value = "W123"
            api.get_worker_summary.return_value = {
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "",
                "is_faculty": True,
                "is_student": False,
                "is_odh_employee": False,
            }
            api.get_net_id_from_worker_id.return_value = "jdoe42"

            response = self.client.post(self.add_url, {"identifier": "222222222"})

        created = User.objects.get(username="222222222")
        self.assertEqual(created.first_name, "Jane")
        self.assertEqual(created.netid, "jdoe42")
        self.assertEqual(created.privilege_level, PrivilegeLevel.INSTRUCTOR)
        mock_update_enrollment.assert_called_once_with(created)
        self.assertRedirects(
            response, reverse("admin:core_user_change", args=(created.pk,))
        )

    def test_enrollment_refresh_failure_surfaces_a_warning(self):
        with (
            patch("yvideo.odhOIDCAuthenticationBackend.Api") as MockApi,
            patch(
                "core.forms.update_user_enrollment",
                return_value={
                    "is_current_sem_updated": False,
                    "is_next_sem_updated": False,
                    "result_message": "Failed to update enrollment.",
                },
            ),
        ):
            api = MockApi.return_value
            api.get_worker_id_from_byu_id.return_value = None
            api.get_student_summary.return_value = {
                "first_name": "Sam",
                "last_name": "Student",
                "email": "",
                "net_id": "sstudent",
            }

            response = self.client.post(
                self.add_url, {"identifier": "555555555"}, follow=True
            )

        self.assertContains(response, "Failed to update enrollment.")

    def test_byu_id_with_no_api_record_shows_error_and_creates_nothing(self):
        with patch("yvideo.odhOIDCAuthenticationBackend.Api") as MockApi:
            api = MockApi.return_value
            api.get_worker_id_from_byu_id.return_value = None
            api.get_student_summary.return_value = None

            response = self.client.post(self.add_url, {"identifier": "333333333"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no record that qualifies")
        self.assertFalse(User.objects.filter(username="333333333").exists())

    def test_byu_id_api_failure_shows_friendly_error_instead_of_500(self):
        with patch("yvideo.odhOIDCAuthenticationBackend.Api") as MockApi:
            MockApi.return_value.get_worker_id_from_byu_id.side_effect = Exception(
                "network error"
            )

            response = self.client.post(self.add_url, {"identifier": "666666666"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Try again in a moment.")
        self.assertFalse(User.objects.filter(username="666666666").exists())

    def test_existing_netid_redirects_to_existing_user(self):
        existing = User.objects.create(username="444444444", netid="bsmith")

        response = self.client.post(self.add_url, {"identifier": "bsmith"})

        self.assertRedirects(
            response, reverse("admin:core_user_change", args=(existing.pk,))
        )

    def test_existing_netid_lookup_is_case_insensitive(self):
        existing = User.objects.create(username="777777777", netid="bsmith")

        response = self.client.post(self.add_url, {"identifier": "BSmith"})

        self.assertRedirects(
            response, reverse("admin:core_user_change", args=(existing.pk,))
        )

    def test_netid_with_student_record_creates_user_via_byu_id_flow(self):
        with (
            patch("core.forms.Api") as FormsMockApi,
            patch("yvideo.odhOIDCAuthenticationBackend.Api") as BackendMockApi,
            patch(
                "core.forms.update_user_enrollment", return_value=ENROLLMENT_OK
            ) as mock_update_enrollment,
        ):
            FormsMockApi.return_value.get_student_summary.return_value = {
                "first_name": "Sam",
                "last_name": "Student",
                "email": "",
                "byu_id": "999999999",
                "net_id": "samstud",
            }

            backend_api = BackendMockApi.return_value
            backend_api.get_worker_id_from_byu_id.return_value = None
            backend_api.get_student_summary.return_value = {
                "first_name": "Sam",
                "last_name": "Student",
                "email": "",
                "byu_id": "999999999",
                "net_id": "samstud",
            }

            response = self.client.post(self.add_url, {"identifier": "samstud"})

        created = User.objects.get(username="999999999")
        self.assertEqual(created.netid, "samstud")
        self.assertEqual(created.privilege_level, PrivilegeLevel.STUDENT)
        mock_update_enrollment.assert_called_once_with(created)
        self.assertRedirects(
            response, reverse("admin:core_user_change", args=(created.pk,))
        )

    def test_netid_without_student_record_reports_not_found_when_api_is_healthy(self):
        course = Course.objects.create(
            dept="ENG", catalog_number="101", section_number="001", yearterm="20261"
        )
        probe_user = User.objects.create(username="121212121", netid="actkid1")
        UserCourses.objects.create(user=probe_user, course=course, yearterm="20261")

        def fake_get_student_summary(net_id=None, byu_id=None):
            if net_id == "actkid1":
                return {
                    "first_name": "Active",
                    "last_name": "Kid",
                    "email": "",
                    "byu_id": "121212121",
                    "net_id": "actkid1",
                }
            return None

        with patch("core.forms.Api") as FormsMockApi:
            FormsMockApi.return_value.get_student_summary.side_effect = (
                fake_get_student_summary
            )
            response = self.client.post(self.add_url, {"identifier": "nonexist"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No BYU student record was found")
        self.assertEqual(User.objects.count(), 2)  # admin + probe only

    def test_netid_lookup_reports_outage_when_health_check_also_fails(self):
        course = Course.objects.create(
            dept="ENG", catalog_number="101", section_number="001", yearterm="20261"
        )
        probe_user = User.objects.create(username="343434343", netid="actkid2")
        UserCourses.objects.create(user=probe_user, course=course, yearterm="20261")

        with patch("core.forms.Api") as FormsMockApi:
            FormsMockApi.return_value.get_student_summary.return_value = None
            response = self.client.post(self.add_url, {"identifier": "nonexist"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "appears to be unavailable")
        self.assertEqual(User.objects.count(), 2)  # admin + probe only

    def test_netid_api_failure_shows_friendly_error_instead_of_500(self):
        with patch("core.forms.Api") as FormsMockApi:
            FormsMockApi.return_value.get_student_summary.side_effect = Exception(
                "network error"
            )
            response = self.client.post(self.add_url, {"identifier": "nonexist"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Try again in a moment.")
        self.assertEqual(User.objects.count(), 1)  # only the logged-in admin

    def test_garbage_identifier_is_rejected(self):
        response = self.client.post(self.add_url, {"identifier": "!!!"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a 9-digit BYU ID or a valid NetID.")
