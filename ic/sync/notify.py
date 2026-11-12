"""Event-triggered pulls: production tells booths that something they mirror changed.

Edits arrive in bursts (an editing session saves on every drag), so changes only
schedule one debounced MediaJob. When it runs, it recomputes the manifest hash and
triggers each pushable instance only if the hash actually moved. A missed or failed
notification just means the booth's 15-minute poll picks the change up instead.
"""

from datetime import timedelta
import logging
import time

from django.db import DatabaseError
from django.db import transaction
from django.db.models.signals import post_delete
from django.db.models.signals import post_save
from django.utils import timezone

logger = logging.getLogger(__name__)

NOTIFY_DEBOUNCE = timedelta(seconds=60)

# Whether any instance can be pushed to, cached briefly so ordinary saves on a server
# with no booth configured do not pay a query each.
_pushable_cache = {"value": None, "checked": 0.0}
_PUSHABLE_CACHE_SECONDS = 60


def _any_pushable_instance():
    now = time.monotonic()
    if (
        _pushable_cache["value"] is None
        or now - _pushable_cache["checked"] > _PUSHABLE_CACHE_SECONDS
    ):
        from ic.models import ICInstance

        try:
            _pushable_cache["value"] = ICInstance.objects.exclude(agent_url="").exists()
        except DatabaseError:
            # The ic tables do not exist yet (fresh install mid-migrate, or a migration
            # test running against an older schema): nothing to notify.
            return False
        _pushable_cache["checked"] = now
    return _pushable_cache["value"]


def reset_pushable_cache():
    _pushable_cache["value"] = None


def schedule_notify():
    """Queue (or postpone) the single pending notify job."""
    from ic.models import MediaJob

    if not _any_pushable_instance():
        return
    not_before = timezone.now() + NOTIFY_DEBOUNCE
    pending = MediaJob.objects.filter(
        kind=MediaJob.Kind.IC_NOTIFY, status=MediaJob.Status.QUEUED
    ).first()
    if pending:
        MediaJob.objects.filter(pk=pending.pk).update(not_before=not_before)
    else:
        MediaJob.objects.create(kind=MediaJob.Kind.IC_NOTIFY, not_before=not_before)


def _on_change(sender, **kwargs):
    if kwargs.get("raw"):
        return
    transaction.on_commit(schedule_notify)


def connect_change_signals():
    from ic.sync.bundle import mirrored_model_classes

    for model in mirrored_model_classes():
        post_save.connect(
            _on_change,
            sender=model,
            weak=False,
            dispatch_uid=f"ic-notify-save-{model.__name__}",
        )
        post_delete.connect(
            _on_change,
            sender=model,
            weak=False,
            dispatch_uid=f"ic-notify-delete-{model.__name__}",
        )
