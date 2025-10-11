import logging

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


class SpoofUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        print("SpoofUserMiddleware __call__", request)
        spoof_user_id = request.session.get("spoof_user_id")
        if spoof_user_id and (
            getattr(request.user, "is_admin", False)
            or getattr(request.user, "is_superuser", False)
        ):
            User = get_user_model()
            try:
                spoofed_user = User.objects.get(pk=spoof_user_id)
                original_user = request.user
                request.user = spoofed_user
                request.is_spoofing = True
                request.original_user = original_user
                logger.info(
                    f"Admin/Superuser {original_user.first_name} {original_user.last_name} ({original_user.netid}) spoofing as {spoofed_user.first_name} {spoofed_user.last_name} ({spoofed_user.netid})"
                )
            except User.DoesNotExist:
                logger.warning(
                    f"Spoofed user {spoof_user_id} does not exist; clearing session"
                )
                request.session.pop("spoof_user_id", None)
                request.is_spoofing = False
                request.original_user = None
        else:
            request.is_spoofing = False
            request.original_user = None
        return self.get_response(request)
