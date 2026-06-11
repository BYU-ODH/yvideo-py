import logging

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


class SpoofUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Determine if the authenticated user has spoofing privileges
        original_user = request.user
        request.can_spoof = original_user.is_authenticated and (
            getattr(original_user, "is_admin", False)
            or getattr(original_user, "is_superuser", False)
        )

        # Check if spoofing is active
        if "spoof_user_id" in request.session and request.can_spoof:
            User = get_user_model()
            try:
                spoofed_user = User.objects.get(pk=request.session["spoof_user_id"])
                request.user = spoofed_user
                request.is_spoofing = True
                request.original_user = original_user
                logger.info(
                    f"Admin/Superuser {original_user.first_name} {original_user.last_name} "
                    f"({original_user.username}) spoofing as {spoofed_user.first_name} "
                    f"{spoofed_user.last_name} ({spoofed_user.username})"
                )
            except User.DoesNotExist:
                logger.warning(
                    f"Spoofed user {request.session['spoof_user_id']} does not exist; clearing spoofing from session"
                )
                request.session.pop("spoof_user_id")
                request.is_spoofing = False
        else:
            request.is_spoofing = False

        return self.get_response(request)
