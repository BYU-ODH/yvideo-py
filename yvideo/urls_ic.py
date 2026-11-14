"""Root urlconf for the booth kiosk (IS_IC_INSTANCE).

Only booth routes exist here: no admin, no login, no editor, no API. The kiosk serves
its own static files from collectstatic's output: there is no Apache on the booth, and
it serves one local browser.
"""

from django.conf import settings
from django.urls import include
from django.urls import path
from django.views.static import serve

from ic.urls import booth_urlpatterns
from ic.views_player import control_index

urlpatterns = [
    path("", control_index, name="index"),
    path("ic/", include(booth_urlpatterns)),
    path("static/<path:path>", serve, {"document_root": settings.STATIC_ROOT}),
]
