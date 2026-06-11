from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from core.api import Api
from core.models import PrivilegeLevel
from core.models import User


class OIDCUserAuth(OIDCAuthenticationBackend):
    def verify_claims(self, claims):
        if "byu_id" in claims or "netid" in claims:
            return True

    def create_user(self, claims):
        byu_id = claims.get("byu_id")
        if byu_id is None:
            return

        # figure out if this is a student or faculty
        api = Api()

        # because faculty members may well have been students, we need to check for faculty status first
        worker_id = api.get_worker_id_from_byu_id(byu_id)
        if worker_id:
            worker_summary = api.get_worker_summary(worker_id, byu_id)
            if worker_summary["is_faculty"]:
                netid = "rencherb"
                # Only activate the following lines after we get access to this API - BDR 6/11/2026
                # netid = api.get_net_id_from_worker_id(self, worker_id)
                # user.netid = netid
                # user.byu_id = byu_id
                # return user

                user = User.objects.create(
                    netid=netid,
                    byu_id=byu_id,
                    privilege_level=PrivilegeLevel.INSTRUCTOR,
                    first_name=worker_summary["first_name"],
                    last_name=worker_summary["last_name"],
                )
                return user
            else:
                # this could be a current student who has a job at BYU, so user the student_summary instead.
                # One problem with this: ODH staff members (or student employees) that need access to this service
                # will not be automatically configured unless we do more to determine who they are with the provided
                # data. Instead, they will have to be manually configured. We will likely want to change this.
                pass

        student_summary = api.get_student_summary(byu_id)
        if student_summary is not None:
            user = User.objects.create(
                netid=student_summary["net_id"],
                byu_id=byu_id,
                first_name=student_summary["first_name"],
                last_name=student_summary["last_name"],
            )
            return user

    def update_user(self, user, claims):
        # for now, we don't do anything to update the user
        # in the future, we may want to update the user's enrolled
        # courses as this point, if they are a student - BDR 6/10/2026
        return user

    def filter_users_by_claims(self, claims):
        byu_id = claims.get("byu_id")
        if not byu_id:
            return User.objects.none()
        try:
            user = User.objects.get(byu_id=byu_id)
            return [user]
        except User.DoesNotExist:
            return User.objects.none()
