"""Root urlconf for the booth sync agent's web role (IS_IC_INSTANCE, IC_ROLE=agent).

Three signed routes and nothing else (IC_MODE_PLAN.md section 8.1).
"""

from django.urls import include
from django.urls import path

urlpatterns = [
    path("ic/", include("ic.urls_agent")),
]
