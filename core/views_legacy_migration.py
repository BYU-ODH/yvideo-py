from django.conf import settings
from django.db import connection
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.http import HttpResponse
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from .forms_legacy_migration import LegacyMigrationRequestForm
from .legacy_migration import LegacyMigrationRequest
from .legacy_migration import LegacyMigrationStatus
from .models import PrivilegeLevel


def _legacy_migration_unavailable_response():
    if not settings.LEGACY_MIGRATION_ENABLED:
        raise Http404("Legacy migration is not enabled.")

    table_name = LegacyMigrationRequest._meta.db_table
    if table_name not in connection.introspection.table_names():
        return HttpResponse(
            "Legacy migration tables are not installed yet. Run "
            "`uv run python manage.py migrate` before using this feature.",
            status=503,
            content_type="text/plain",
        )
    return None


def _can_request_migration(user):
    return (
        user.privilege_level == PrivilegeLevel.INSTRUCTOR
        or user.privilege_level_override == PrivilegeLevel.INSTRUCTOR
        or user.is_staff
        or user.is_superuser
        or user.is_lab_assistant
    )


@require_GET
def legacy_migration_requests(request):
    unavailable_response = _legacy_migration_unavailable_response()
    if unavailable_response:
        return unavailable_response
    if not _can_request_migration(request.user):
        return HttpResponseForbidden(
            "You do not have permission to request a migration."
        )

    requests = (
        LegacyMigrationRequest.objects.filter(
            Q(requested_by=request.user) | Q(target_owner=request.user)
        )
        .distinct()
        .order_by("-created_at")
    )
    return render(
        request,
        "core/legacy_migration_requests.html",
        {
            "form": LegacyMigrationRequestForm(),
            "migration_requests": requests,
        },
    )


@require_POST
def create_legacy_migration_request(request):
    unavailable_response = _legacy_migration_unavailable_response()
    if unavailable_response:
        return unavailable_response
    if not _can_request_migration(request.user):
        return HttpResponseForbidden(
            "You do not have permission to request a migration."
        )

    form = LegacyMigrationRequestForm(request.POST)
    if not form.is_valid():
        requests = (
            LegacyMigrationRequest.objects.filter(
                Q(requested_by=request.user) | Q(target_owner=request.user)
            )
            .distinct()
            .order_by("-created_at")
        )
        return render(
            request,
            "core/legacy_migration_requests.html",
            {
                "form": form,
                "migration_requests": requests,
            },
            status=400,
        )

    migration_request = form.save(commit=False)
    migration_request.requested_by = request.user
    migration_request.target_owner = request.user
    if not migration_request.target_collection_name:
        migration_request.target_collection_name = ""
    with transaction.atomic():
        migration_request.save()
        migration_request.status = LegacyMigrationStatus.SUBMITTED
        migration_request.save(update_fields=["status", "updated_at"])
        migration_request.queue_job("preflight")
    return redirect("legacy_migration_request_detail", pk=migration_request.pk)


@require_GET
def legacy_migration_request_detail(request, pk):
    unavailable_response = _legacy_migration_unavailable_response()
    if unavailable_response:
        return unavailable_response
    migration_request = get_object_or_404(LegacyMigrationRequest, pk=pk)
    if not (
        request.user.is_staff
        or request.user.is_superuser
        or request.user.is_lab_assistant
        or migration_request.requested_by_id == request.user.id
        or migration_request.target_owner_id == request.user.id
    ):
        return HttpResponseForbidden("You do not have permission to view this request.")

    return render(
        request,
        "core/legacy_migration_request_detail.html",
        {
            "migration_request": migration_request,
        },
    )
