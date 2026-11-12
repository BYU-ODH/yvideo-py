"""Root urlconf for the booth kiosk (IS_IC_INSTANCE, IC_ROLE=kiosk).

Only booth routes exist here: no admin, no login, no editor, no API. The kiosk serves
its own static files (there is no Apache on the booth).
"""

from django.shortcuts import redirect
from django.urls import include
from django.urls import path
from django.urls import re_path

from ic.views_booth import booth_view
from ic.views_booth import static_file


@booth_view
def root(request):
    return redirect("ic_control_index")


root.permission_check = "booth: redirect to the kiosk"

urlpatterns = [
    path("", root, name="index"),
    path("ic/", include("ic.urls_kiosk")),
    re_path(r"^static/(?P<path>.+)$", static_file, name="ic_static"),
]
