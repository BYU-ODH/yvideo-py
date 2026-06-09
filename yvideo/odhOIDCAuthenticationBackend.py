import logging

from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from core.models import User

logger = logging.getLogger(__name__)


class OIDCUserAuth(OIDCAuthenticationBackend):
    def verify_claims(self, claims):
        if "byu_id" in claims and "netid" in claims:
            return True
        else:
            logger.warning(
                "OIDC claims missing byu_id and/or netid; received claims: %s",
                sorted(claims.keys()),
            )

    def create_user(self, claims):
        user = super().create_user(claims)
        user.netid = claims.get("netid")
        user.byu_id = claims.get("byu_id")
        user.save()
        return user

    def update_user(self, user, claims):
        user.netid = claims.get("netid")
        user.byu_id = claims.get("byu_id")
        user.save()
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
