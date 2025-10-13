from django.contrib.auth.backends import BaseBackend

from core.models import User


class CustomAuth(BaseBackend):
    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

    def authenticate(self, request, byu_id):
        try:
            user = User.objects.get(byu_id=byu_id)
        except User.DoesNotExist:
            return None

        return self.get_user(user_id=user.pk)
