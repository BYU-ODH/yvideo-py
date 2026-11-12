"""The booth sync agent's /ic/ routes (IC_ROLE=agent, LAN port open to production only)."""

from django.urls import path

from . import views_agent

urlpatterns = [
    path("agent/sync/", views_agent.sync_trigger, name="ic_agent_sync"),
    path(
        "agent/sync/<uuid:run_id>/",
        views_agent.sync_run_progress,
        name="ic_agent_sync_progress",
    ),
    path("agent/status/", views_agent.agent_status, name="ic_agent_status"),
]
