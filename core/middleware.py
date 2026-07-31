import logging

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


class SpoofUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Determine if the authenticated user has spoofing privileges
        original_user = request.user
        request.can_spoof = original_user.is_authenticated and getattr(
            original_user, "can_spoof", False
        )

        # Check if spoofing is active
        if "spoof_user_id" in request.session and request.can_spoof:
            User = get_user_model()
            try:
                spoofed_user = User.objects.get(pk=request.session["spoof_user_id"])
            except User.DoesNotExist:
                logger.warning(
                    f"Spoofed user {request.session['spoof_user_id']} does not exist; clearing spoofing from session"
                )
                request.session.pop("spoof_user_id")
                request.is_spoofing = False
            else:
                if original_user.can_spoof_as(spoofed_user):
                    request.user = spoofed_user
                    request.is_spoofing = True
                    request.original_user = original_user
                    role = "Admin" if original_user.is_admin else "Lab Assistant"
                    logger.info(
                        f"SPOOF ACTIVE: {role} {original_user.first_name} {original_user.last_name} "
                        f"({original_user.netid} {original_user.username}) is spoofing as "
                        f"{spoofed_user.first_name} {spoofed_user.last_name} "
                        f"({spoofed_user.netid} {spoofed_user.username}) "
                        f"[{request.method} {request.path}]"
                    )
                else:
                    logger.warning(
                        f"SPOOF DENIED: {original_user.first_name} {original_user.last_name} "
                        f"({original_user.netid} {original_user.username}) is not permitted to spoof "
                        f"{spoofed_user.first_name} {spoofed_user.last_name} "
                        f"({spoofed_user.netid} {spoofed_user.username}); clearing spoofing from session"
                    )
                    request.session.pop("spoof_user_id")
                    request.is_spoofing = False
        else:
            request.is_spoofing = False

        return self.get_response(request)
