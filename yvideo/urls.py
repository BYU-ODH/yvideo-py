"""URL configuration for yvideo project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/

Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))

"""

from django.contrib import admin
from django.contrib.auth.decorators import login_not_required
from django.urls import include
from django.urls import path
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView
from mozilla_django_oidc.views import OIDCAuthenticationRequestView
from mozilla_django_oidc.views import OIDCLogoutView

from core.urls import urlpatterns as core_urlpatterns
from yvideo.views import dev_quick_login

# The OIDC login + callback views must be reachable while the user is still
# unauthenticated, so they have to be exempt from LoginRequiredMiddleware.
# Without this the middleware redirects /oidc/authenticate/ back to itself,
# producing an infinite (and ever-growing ?next=...) redirect loop. Names are
# kept flat (no namespace) so reverse("oidc_authentication_init") still works.
#
# NOTE: We reference mozilla_django_oidc's default view classes directly rather
# than include()-ing its urlconf so we can wrap them in login_not_required. If
# you ever set OIDC_CALLBACK_CLASS or OIDC_AUTHENTICATE_CLASS in settings, those
# custom classes must be imported and used here instead of the defaults below.
oidc_urlpatterns = [
    path(
        "callback/",
        login_not_required(OIDCAuthenticationCallbackView.as_view()),
        name="oidc_authentication_callback",
    ),
    path(
        "authenticate/",
        login_not_required(OIDCAuthenticationRequestView.as_view()),
        name="oidc_authentication_init",
    ),
    path("logout/", OIDCLogoutView.as_view(), name="oidc_logout"),
]

urlpatterns = core_urlpatterns

urlpatterns.extend(
    [
        path("oidc/", include(oidc_urlpatterns)),
        path("login/dev/quick/", dev_quick_login, name="dev_quick_login"),
        path("admin/", admin.site.urls),
    ]
)
