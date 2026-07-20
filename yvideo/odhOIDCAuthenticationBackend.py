from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from core.api import Api
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


class OIDCUserAuth(OIDCAuthenticationBackend):
    def verify_claims(self, claims):
        if "byu_id" in claims:
            api = Api()
            worker_id = api.get_worker_id_from_byu_id(claims.get("byu_id"))
            student_summary = api.get_student_summary(claims.get("byu_id"))
            if worker_id is None and student_summary is None:
                print(
                    f"Login rejected because the user is not affiliated as an employee or student. Rejected BYU-ID: {claims.get('byu_id')}"
                )
                return False
            return True
        else:
            print("Login rejected. No BYU-ID provided")
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
        is_admin = byu_id in getattr(secret_settings, "ADMIN_BYUID_WHITELIST", [])
        if worker_id:
            # by default, we give the least privileges to users. If a user should have admin
            # privileges, they should be manually elevated
            worker_summary = api.get_worker_summary(worker_id, byu_id)
            if worker_summary["is_faculty"]:
                user = User.objects.create(
                    username=byu_id,
                    netid=api.get_net_id_from_worker_id(worker_id),
                    privilege_level=PrivilegeLevel.ADMIN
                    if is_admin
                    else PrivilegeLevel.INSTRUCTOR,
                    first_name=worker_summary["first_name"],
                    last_name=worker_summary["last_name"],
                    is_staff=worker_summary["is_odh_employee"] or is_admin,
                )
                return user
            elif not worker_summary["is_student"] and worker_summary["is_odh_employee"]:
                user = User.objects.create(
                    username=byu_id,
                    netid=api.get_net_id_from_worker_id(worker_id),
                    first_name=worker_summary["first_name"],
                    last_name=worker_summary["last_name"],
                    is_staff=is_admin,
                    privilege_level=(
                        PrivilegeLevel.ADMIN if is_admin else PrivilegeLevel.STUDENT
                    ),
                )
                return user

        student_summary = api.get_student_summary(byu_id)
        if student_summary is not None:
            user = User.objects.create(
                username=byu_id,
                netid=student_summary["net_id"],
                first_name=student_summary["first_name"],
                last_name=student_summary["last_name"],
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
        try:
            user = User.objects.get(username=byu_id)
            return [user]
        except User.DoesNotExist:
            return User.objects.none()
