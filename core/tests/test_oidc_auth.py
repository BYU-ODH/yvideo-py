from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from core.models import PrivilegeLevel
from core.models import User
from yvideo import odhOIDCAuthenticationBackend as oidc_module
from yvideo.odhOIDCAuthenticationBackend import OIDCUserAuth


class CreateUserTests(TestCase):
    """Covers the account-creation path exercised the first time a user logs in."""

    def _worker_summary(self, **overrides):
        summary = {
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "",
            "is_faculty": False,
            "is_student": False,
            "is_odh_employee": False,
        }
        summary.update(overrides)
        return summary

    @patch("yvideo.odhOIDCAuthenticationBackend.Api")
    def test_faculty_user_gets_netid_from_worker_id(self, MockApi):
        api = MockApi.return_value
        api.get_worker_id_from_byu_id.return_value = "W123"
        api.get_worker_summary.return_value = self._worker_summary(is_faculty=True)
        api.get_net_id_from_worker_id.return_value = "jdoe42"

        with patch.object(
            oidc_module,
            "secret_settings",
            SimpleNamespace(ADMIN_BYUID_WHITELIST=[]),
        ):
            user = OIDCUserAuth().create_user({"byu_id": "byu-1"})

        self.assertIsNotNone(user)
        self.assertEqual(user.netid, "jdoe42")
        self.assertEqual(user.privilege_level, PrivilegeLevel.INSTRUCTOR)
        # Guards against regressing the bug where the bound method was called as
        # get_net_id_from_worker_id(self, worker_id).
        api.get_net_id_from_worker_id.assert_called_once_with("W123")
        self.assertTrue(User.objects.filter(username="byu-1").exists())

    @patch("yvideo.odhOIDCAuthenticationBackend.Api")
    def test_whitelisted_byu_id_becomes_admin(self, MockApi):
        api = MockApi.return_value
        api.get_worker_id_from_byu_id.return_value = "W123"
        api.get_worker_summary.return_value = self._worker_summary(is_faculty=True)
        api.get_net_id_from_worker_id.return_value = "jdoe42"

        with patch.object(
            oidc_module,
            "secret_settings",
            SimpleNamespace(ADMIN_BYUID_WHITELIST=["byu-admin"]),
        ):
            user = OIDCUserAuth().create_user({"byu_id": "byu-admin"})

        self.assertEqual(user.privilege_level, PrivilegeLevel.ADMIN)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    @patch("yvideo.odhOIDCAuthenticationBackend.Api")
    def test_missing_admin_whitelist_does_not_raise(self, MockApi):
        api = MockApi.return_value
        api.get_worker_id_from_byu_id.return_value = "W123"
        api.get_worker_summary.return_value = self._worker_summary(is_faculty=True)
        api.get_net_id_from_worker_id.return_value = "jdoe42"

        # secret_settings without ADMIN_BYUID_WHITELIST, mirroring an environment
        # whose secret_settings.py predates the setting.
        with patch.object(oidc_module, "secret_settings", SimpleNamespace()):
            user = OIDCUserAuth().create_user({"byu_id": "byu-2"})

        self.assertIsNotNone(user)
        self.assertEqual(user.privilege_level, PrivilegeLevel.INSTRUCTOR)
        self.assertFalse(user.is_staff)

    @patch("yvideo.odhOIDCAuthenticationBackend.Api")
    def test_student_user_gets_netid_from_student_summary(self, MockApi):
        api = MockApi.return_value
        api.get_worker_id_from_byu_id.return_value = None
        api.get_student_summary.return_value = {
            "first_name": "Sam",
            "last_name": "Student",
            "net_id": "sstudent",
        }

        with patch.object(
            oidc_module,
            "secret_settings",
            SimpleNamespace(ADMIN_BYUID_WHITELIST=[]),
        ):
            user = OIDCUserAuth().create_user({"byu_id": "byu-3"})

        self.assertIsNotNone(user)
        self.assertEqual(user.netid, "sstudent")
        self.assertEqual(user.privilege_level, PrivilegeLevel.STUDENT)
        api.get_net_id_from_worker_id.assert_not_called()

    @patch("yvideo.odhOIDCAuthenticationBackend.Api")
    def test_returns_none_without_byu_id(self, MockApi):
        user = OIDCUserAuth().create_user({})

        self.assertIsNone(user)
        MockApi.assert_not_called()


class UpdateUserTests(TestCase):
    """Covers the path exercised when an existing user logs in."""

    def test_whitelisted_existing_user_is_elevated(self):
        user = User.objects.create_user(
            username="byu-admin", privilege_level=PrivilegeLevel.INSTRUCTOR
        )

        with patch.object(
            oidc_module,
            "secret_settings",
            SimpleNamespace(ADMIN_BYUID_WHITELIST=["byu-admin"]),
        ):
            OIDCUserAuth().update_user(user, {"byu_id": "byu-admin"})

        user.refresh_from_db()
        self.assertEqual(user.privilege_level, PrivilegeLevel.ADMIN)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_non_whitelisted_existing_user_is_not_elevated(self):
        user = User.objects.create_user(
            username="byu-1", privilege_level=PrivilegeLevel.INSTRUCTOR
        )

        with patch.object(
            oidc_module,
            "secret_settings",
            SimpleNamespace(ADMIN_BYUID_WHITELIST=["byu-admin"]),
        ):
            OIDCUserAuth().update_user(user, {"byu_id": "byu-1"})

        user.refresh_from_db()
        self.assertEqual(user.privilege_level, PrivilegeLevel.INSTRUCTOR)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
