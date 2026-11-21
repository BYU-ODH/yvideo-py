import logging

from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from core.api import Api
from core.api import ApiUnavailable
from core.legacy_migration import LEGACY_USERNAME_PREFIX
from core.model_utils import update_user_details
from core.model_utils import update_user_enrollment
from core.models import PrivilegeLevel
from core.models import User

try:
    from . import secret_settings
except ImportError:
    import warnings

    warnings.warn(
        "secret_settings.py not found; falling back to secret_settings_template.py",
        stacklevel=1,
    )
    from . import secret_settings_template as secret_settings

logger = logging.getLogger(__name__)


class OIDCUserAuth(OIDCAuthenticationBackend):
    def verify_claims(self, claims):
        if "byu_id" in claims:
            api = Api()
            worker_id = api.get_worker_id_from_byu_id(claims.get("byu_id"))
            student_summary = api.get_student_summary(claims.get("byu_id"))
            if not worker_id and student_summary is None:
                print(
                    f"Login rejected because the user is not affiliated as an employee or student. Rejected BYU-ID: {claims.get('byu_id')}"
                )
                return False
            return True
        else:
            logger.warning(
                "OIDC claims missing byu_id; received claims: %s",
                sorted(claims.keys()),
            )
            return False

    def create_user(self, claims):
        byu_id = claims.get("byu_id")
        if byu_id is None:
            print("Failed to create a user, no BYU-ID provided")
            return

        api = Api()

        # because faculty members may well have been students, we need to check for faculty status first
        # so that they are not accidentally assigned as students
        worker_id = api.get_worker_id_from_byu_id(byu_id)
        is_whitelist_admin = byu_id in getattr(
            secret_settings, "ADMIN_BYUID_WHITELIST", []
        )
        if worker_id:
            # by default, we give the least privileges to users. If a user should have admin
            # privileges, they should be manually elevated
            worker_summary = api.get_worker_summary(worker_id, byu_id)
            if worker_summary["is_faculty"]:
                user = User.objects.create(
                    username=byu_id,
                    netid=api.get_net_id_from_worker_id(worker_id),
                    privilege_level=PrivilegeLevel.INSTRUCTOR,
                    first_name=worker_summary["first_name"],
                    last_name=worker_summary["last_name"],
                    is_staff=worker_summary["is_odh_employee"] or is_whitelist_admin,
                    is_superuser=is_whitelist_admin,
                )
                return user
            elif not worker_summary["is_student"] and worker_summary["is_odh_employee"]:
                user = User.objects.create(
                    username=byu_id,
                    netid=api.get_net_id_from_worker_id(worker_id),
                    first_name=worker_summary["first_name"],
                    last_name=worker_summary["last_name"],
                    is_staff=is_whitelist_admin,
                    is_superuser=is_whitelist_admin,
                    privilege_level=PrivilegeLevel.STUDENT,
                )
                return user

        student_summary = api.get_student_summary(byu_id)
        if student_summary is not None:
            user = User.objects.create(
                username=byu_id,
                netid=student_summary["net_id"],
                first_name=student_summary["first_name"],
                last_name=student_summary["last_name"],
                is_staff=is_whitelist_admin,
                is_superuser=is_whitelist_admin,
            )
            return user

    def update_user(self, user, claims):
        update_user_details(user)
        if user.privilege_level == PrivilegeLevel.STUDENT:
            update_user_enrollment(user)
        return user

    def filter_users_by_claims(self, claims):
        byu_id = claims.get("byu_id")
        if not byu_id:
            return User.objects.none()
        user = User.objects.filter(
            username=byu_id
        ).first() or self._adopt_imported_user(byu_id)
        return [user] if user else User.objects.none()

    @staticmethod
    def _adopt_imported_user(byu_id):
        """Give this person the legacy-imported account that matches their netid.

        Imported accounts carry a placeholder username, so they can only be found
        by netid. Accounts with a real username are never matched this way.
        """
        imported = User.objects.filter(username__startswith=LEGACY_USERNAME_PREFIX)
        if not imported.exists():
            return None
        api = Api()
        # If BYU's API fails here, stop rather than let the caller create a new
        # account, because that account would hide the imported one for good.
        worker_id = api.get_worker_id_from_byu_id(byu_id)
        if worker_id is None:
            raise ApiUnavailable("Could not look up this user's worker ID.")
        if worker_id:
            netid = api.get_net_id_from_worker_id(worker_id)
            if not netid:
                raise ApiUnavailable("Could not look up this worker's netid.")
        else:
            netid = (api.get_student_summary(byu_id) or {}).get("net_id")
            if not netid:
                return None
        matches = list(imported.filter(netid__iexact=netid)[:2])
        if len(matches) != 1:
            return None
        user = matches[0]
        user.username = byu_id
        user.save(update_fields=["username"])
        return user
