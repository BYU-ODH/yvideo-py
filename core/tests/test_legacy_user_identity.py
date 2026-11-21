from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch

from django.test import TransactionTestCase

from core.api import ApiUnavailable
from core.forms import AddUserLookupForm
from core.legacy_migration import LegacySourceMap
from core.legacy_migration.bulk_import import REQUIRED_COLUMNS
from core.legacy_migration.bulk_import import LegacyBulkImporter
from core.models import Playlist
from core.models import PrivilegeLevel
from core.models import User
from yvideo.odhOIDCAuthenticationBackend import OIDCUserAuth

BYU_ID = "111222333"
PERSON_ID = "999888777"


class LegacyUserIdentityTests(TransactionTestCase):
    """Imported legacy users must end up as the account they log in to."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.source = Path(tmp.name) / "legacy.sqlite"
        with sqlite3.connect(self.source) as db:
            for table, columns in REQUIRED_COLUMNS.items():
                names = ["id", "deleted", "created", "updated", *sorted(columns)]
                db.execute(f'CREATE TABLE "{table}" ({", ".join(names)})')
            db.execute(
                "INSERT INTO users (id, username, byu_person_id, email, account_name, account_type)"
                " VALUES ('u1', 'jdoe', ?, '', 'Jane Doe', 1)",
                [PERSON_ID],
            )
            db.execute(
                "INSERT INTO collections (id, collection_name, owner, published, archived)"
                " VALUES ('c1', 'Spanish 101', 'u1', 'true', 'false')"
            )

    def _import(self):
        LegacyBulkImporter(source=self.source, yearterm="20265", commit=True).run()

    def _login(self, netid="jdoe"):
        with patch("yvideo.odhOIDCAuthenticationBackend.Api") as MockApi:
            api = MockApi.return_value
            api.get_worker_id_from_byu_id.return_value = "W1"
            api.get_net_id_from_worker_id.return_value = netid
            return list(OIDCUserAuth().filter_users_by_claims({"byu_id": BYU_ID}))

    def test_adding_imported_user_by_byu_id_uses_their_imported_account(self):
        self._import()

        with patch("yvideo.odhOIDCAuthenticationBackend.Api") as MockApi:
            api = MockApi.return_value
            api.get_worker_id_from_byu_id.return_value = "W1"
            api.get_net_id_from_worker_id.return_value = "jdoe"
            form = AddUserLookupForm({"identifier": BYU_ID})
            self.assertTrue(form.is_valid(), form.errors)

        self.assertFalse(form.created)
        self.assertEqual(self._login(), [form.resolved_user])
        self.assertEqual(User.objects.count(), 1)

    def test_login_is_refused_when_netid_lookup_fails(self):
        self._import()

        with self.assertRaises(ApiUnavailable):
            self._login(netid=None)
        self.assertEqual(User.objects.get().username, "legacy:jdoe")

    def test_imported_owner_logs_in_to_their_imported_account(self):
        self._import()

        self.assertEqual(self._login(), [User.objects.get(netid="jdoe")])
        user = User.objects.get(netid="jdoe")
        self.assertEqual(user.username, BYU_ID)
        self.assertTrue(user.is_instructor)
        self.assertEqual(
            list(Playlist.objects.filter(owner=user).values_list("name", flat=True)),
            ["Spanish 101"],
        )

    def test_import_reuses_existing_user_with_same_netid(self):
        existing = User.objects.create(username=BYU_ID, netid="jdoe")

        self._import()
        self.assertEqual(self._login(), [existing])

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Playlist.objects.get().owner, existing)

    def test_login_never_adopts_a_real_account_by_netid(self):
        other = User.objects.create(username="444555666", netid="jdoe")

        self.assertEqual(self._login(), [])
        other.refresh_from_db()
        self.assertEqual(other.username, "444555666")

    def test_login_ignores_imported_account_with_another_netid(self):
        self._import()

        self.assertEqual(self._login(netid="someone"), [])
        self.assertEqual(User.objects.get(netid="jdoe").username, "legacy:jdoe")

    def test_reimport_after_login_creates_no_duplicates(self):
        self._import()
        self._login()

        self._import()
        LegacySourceMap.objects.all().delete()
        self._import()

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(User.objects.get().username, BYU_ID)

    def test_staff_owner_keeps_explicit_override(self):
        User.objects.create(
            username=BYU_ID,
            netid="jdoe",
            privilege_level_override=PrivilegeLevel.STUDENT,
        )

        self._import()

        self.assertFalse(User.objects.get().is_instructor)
