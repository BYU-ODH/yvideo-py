from django.contrib.auth import login as django_login
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from core.dev_features import DEMO_ADMIN_NETID
from core.dev_features import is_dev_quick_login_enabled
from core.dev_features import is_local_dev_host
from core.models import User

django_login = login_not_required(django_login)


def oidc_login(request):
    if request.user.is_authenticated:
        return HttpResponseRedirect("/collections")
    return HttpResponseRedirect(reverse("oidc_authentication_init"))


@login_not_required
def dev_quick_login(request):
    if not is_dev_quick_login_enabled() or not is_local_dev_host(request.get_host()):
        return HttpResponse(status=404)

    try:
        admin_user = User.objects.get(
            netid=DEMO_ADMIN_NETID,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
    except User.DoesNotExist:
        return HttpResponse(
            "Demo admin account not found. Run `uv run manage.py seed_demo_data` first.",
            status=409,
        )

    next_url = request.GET.get("next") or "/"
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = "/"

    request.session.flush()
    django_login(
        request=request,
        user=admin_user,
        backend="django.contrib.auth.backends.ModelBackend",
    )
    return HttpResponseRedirect(next_url)
