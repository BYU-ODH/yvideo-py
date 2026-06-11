from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from core.api import Api
from core.models import User


class OIDCUserAuth(OIDCAuthenticationBackend):
    def verify_claims(self, claims):
        if "byu_id" in claims or "netid" in claims:
            return True

    def create_user(self, claims):
        user = super().create_user(claims)
        netid = claims.get("netid")
        byu_id = claims.get("byu_id")
        if netid is not None and byu_id is not None:
            user.netid = netid
            user.byu_id = byu_id
        elif byu_id is not None:
            api = Api()
            student_summary = api.get_student_summary(byu_id)
            if student_summary is not None:
                user.netid = student_summary["net_id"]
                user.byu_id = byu_id
            else:
                # this must be an employee/facutly, or is not effectively affiliated with BYU
                worker_id = api.get_worker_id_from_byu_id(byu_id)
                if worker_id is None:
                    # this is unlikely to happen, but don't create a user for this person
                    # because they are not a student, and are not an employee/faculty
                    user.delete()
                    return
                # we don't get netid from worker_summary, but we can get the first and last name
                #
                # Only activate the following lines after we get access to this API - BDR 6/11/2026
                # netid = api.get_net_id_from_worker_id(self, worker_id)
                # user.netid = netid
                # user.byu_id = byu_id
        else:
            # somehow this person got to this point without a valid byuid. Not sure how that could happen
            # but don't build a user for this person/entity
            user.delete()
            return

        user.save()
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
