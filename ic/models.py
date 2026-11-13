"""International Cinema (#246): programs, the media pipeline, and booth sync.

Production-side tables: ICProgram, ICProgramItem, ICUpload, MediaJob, ICBoothReport.
ICProgram and ICProgramItem are also mirrored to the booth.

Booth-local tables (never mirrored): ICSyncRun, ICLocalFile, ICBoothState.
"""

from datetime import timedelta
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Content
from core.models import Resource
from core.models import ResourceFile

# A program stays on the booth, and playable, through the night after its last day.
LATE_SHOW_GRACE = timedelta(days=1)


class ICProgram(models.Model):
    """A run of a film (or several) between two dates, in playback order."""

    name = models.CharField(max_length=255)
    start_date = models.DateField()
    end_date = models.DateField()
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "name", "id"]
        verbose_name = "IC program"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="ic_program_end_on_or_after_start",
                violation_error_message="The program can't end before it starts.",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.start_date} – {self.end_date})"

    @classmethod
    def syncable(cls, on=None):
        """Active and upcoming programs, plus yesterday's, so a late show is not pulled."""
        on = on or timezone.localdate()
        return cls.objects.filter(end_date__gte=on - LATE_SHOW_GRACE)

    def is_syncable(self, on=None):
        """The same rule as syncable(): what the booth holds and will play."""
        on = on or timezone.localdate()
        return self.end_date >= on - LATE_SHOW_GRACE

    def is_active(self, on=None):
        on = on or timezone.localdate()
        return self.start_date <= on <= self.end_date

    def is_upcoming(self, on=None):
        on = on or timezone.localdate()
        return self.start_date > on

    def is_past(self, on=None):
        on = on or timezone.localdate()
        return self.end_date < on

    def ordered_items(self):
        return self.items.select_related(
            "content",
            "content__resource",
            "content__resource_file",
            "content__playlist",
            "content__default_subtitle_track",
        ).order_by("position", "id")

    def renumber_items(self):
        for position, item in enumerate(self.items.order_by("position", "id")):
            if item.position != position:
                item.position = position
                item.save(update_fields=["position"])


class ICProgramItem(models.Model):
    """One thing the projector shows: an announcement loop, a short, a feature."""

    class Mode(models.TextChoices):
        ONCE = "once", "Play once"
        LOOP = "loop", "Loop until advanced"

    class Subtitles(models.TextChoices):
        VTT = "vtt", "Show the content's default subtitle track"
        BURNED_IN = "burned_in", "Burned into the master"
        NONE = "none", "No subtitles"

    program = models.ForeignKey(
        ICProgram, on_delete=models.CASCADE, related_name="items"
    )
    content = models.ForeignKey(
        Content,
        on_delete=models.PROTECT,
        related_name="ic_program_items",
        limit_choices_to={"playlist__is_ic": True},
    )
    position = models.PositiveIntegerField(default=0)
    mode = models.CharField(max_length=4, choices=Mode.choices, default=Mode.ONCE)
    auto_advance = models.BooleanField(
        default=False, help_text="When a play-once item ends, start the next one."
    )
    subtitles = models.CharField(
        max_length=9, choices=Subtitles.choices, default=Subtitles.VTT
    )
    timing_verified = models.BooleanField(
        default=False,
        help_text=(
            "An IC editor checked that the annotations line up with the projection "
            "master, even though the editing file was not transcoded from it."
        ),
    )

    class Meta:
        ordering = ["position", "id"]
        verbose_name = "IC program item"

    def __str__(self):
        return f"{self.program.name} #{self.position + 1}: {self.content.title}"

    def projection_file(self):
        if not self.content.resource_id:
            return None
        return ResourceFile.objects.filter(
            resource_id=self.content.resource_id, is_projection_file=True
        ).first()

    def subtitle_track(self):
        if self.subtitles != self.Subtitles.VTT:
            return None
        return self.content.default_subtitle_track


class MediaJob(models.Model):
    """Background work on production, run by `manage.py process_media_jobs`."""

    class Kind(models.TextChoices):
        INGEST = "ingest", "Ingest uploaded master"
        TRANSCODE = "transcode", "Transcode SD editing file"

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.QUEUED
    )
    payload = models.JSONField(default=dict, blank=True)
    resource_file = models.ForeignKey(
        ResourceFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="media_jobs",
    )
    progress = models.FloatField(null=True, blank=True)
    log = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.get_kind_display()} [{self.status}] #{self.pk}"

    @property
    def is_pending(self):
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)


class ICUpload(models.Model):
    """A chunked, resumable upload of a projection master."""

    class Status(models.TextChoices):
        UPLOADING = "uploading"
        PROCESSING = "processing"
        DONE = "done"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource = models.ForeignKey(
        Resource, on_delete=models.CASCADE, related_name="ic_uploads"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="ic_uploads",
    )
    filename = models.CharField(max_length=255)
    expected_size = models.PositiveBigIntegerField()
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.UPLOADING
    )
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.filename} → {self.resource.name} ({self.status})"

    @property
    def part_path(self):
        return settings.IC_PROJECTION_ROOT / ".incoming" / f"{self.pk}.part"

    @property
    def received_bytes(self):
        """What has arrived so far: the .part file is the record."""
        try:
            return self.part_path.stat().st_size
        except FileNotFoundError:
            return 0


# How often a booth asks production whether anything changed (a cheap request).
CHECK_INTERVAL_SECONDS = 30
# An instance that has missed this many checks in a row counts as out of contact.
CHECK_IN_GRACE_INTERVALS = 4


class ICBoothReport(models.Model):
    """Singleton on production: when the booth last called, and its last run report.

    There is one booth. It authenticates with settings.IC_SYNC_TOKEN, checks for changes
    every CHECK_INTERVAL_SECONDS, and reports each run's outcome, including every
    projection master it holds a verified copy of. Production never connects to it.
    """

    last_seen_at = models.DateTimeField(null=True, blank=True)
    report = models.JSONField(default=dict, blank=True)

    @classmethod
    def get(cls):
        """The booth's record (unsaved and empty if it has never called)."""
        return cls.objects.filter(pk=1).first() or cls(pk=1)

    @classmethod
    def seen(cls):
        cls.objects.update_or_create(pk=1, defaults={"last_seen_at": timezone.now()})

    @property
    def applied_manifest_hash(self):
        return self.report.get("applied_manifest_hash", "")

    def checked_in_recently(self, now=None):
        """Whether the booth has called production within a few check intervals."""
        if self.last_seen_at is None:
            return False
        now = now or timezone.now()
        return now - self.last_seen_at <= timedelta(
            seconds=CHECK_INTERVAL_SECONDS * CHECK_IN_GRACE_INTERVALS
        )

    def holds_verified_copy(self, master):
        """Whether the booth's last report lists a copy of master with its checksum.

        The booth records a file only after hashing its bytes and finding a match, and
        drops the record when a re-hash fails.
        """
        return any(
            f.get("resource_file_id") == master.pk and f.get("xxh64") == master.checksum
            for f in self.report.get("verified_files", [])
        )


# ---------------------------------------------------------------------------
# Booth-local tables
# ---------------------------------------------------------------------------


class ICSyncRun(models.Model):
    """One sync on the booth, whatever started it."""

    class Trigger(models.TextChoices):
        # The agent's check found something to do: a change on production, a retry, or
        # the weekly re-hash.
        AUTO = "auto", "Automatic"
        FORCE_PULL = "force_pull", "Sync now"

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        PARTIAL = "partial"
        FAILED = "failed"

    FINAL_STATUSES = (Status.SUCCEEDED, Status.PARTIAL, Status.FAILED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trigger = models.CharField(max_length=10, choices=Trigger.choices)
    # Hash every file on disk instead of trusting earlier verification. Only the
    # agent's weekly morning re-hash of released masters sets this.
    full_rehash = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED
    )
    # What the run is doing now, for the operator ("Downloading", "Importing", ...).
    phase = models.CharField(max_length=40, blank=True)
    bytes_done = models.BigIntegerField(default=0)
    bytes_total = models.BigIntegerField(default=0)
    manifest_hash = models.CharField(max_length=64, blank=True)
    report = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.trigger} {self.status} {self.requested_at:%Y-%m-%d %H:%M}"

    @property
    def is_final(self):
        return self.status in self.FINAL_STATUSES

    @classmethod
    def latest_finished(cls, **filters):
        return (
            cls.objects.filter(finished_at__isnull=False, **filters)
            .order_by("-finished_at")
            .first()
        )


class ICLocalFile(models.Model):
    """A file the booth holds and has verified against production's checksum."""

    class Kind(models.TextChoices):
        PROJECTION = "projection"
        SUBTITLE = "subtitle"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    ref_id = models.BigIntegerField()
    name = models.CharField(max_length=255)
    size = models.BigIntegerField()
    xxh64 = models.CharField(max_length=16)
    verified_at = models.DateTimeField()
    # Released masters exist nowhere else, which changes how they may be pruned.
    available_from_parent = models.BooleanField(default=True)
    last_program_end = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = ("kind", "ref_id")

    def __str__(self):
        return f"{self.kind}:{self.ref_id} {self.name}"


# The kiosk counts the sync agent as running if it beat its heartbeat this recently.
AGENT_ALIVE_SECONDS = 30


class ICBoothState(models.Model):
    """Singleton: what the booth last applied, what production last said, and whether
    the sync agent is running."""

    applied_manifest_hash = models.CharField(max_length=64, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    # What production's manifest hash was at the agent's last check, so the controller
    # can say whether production has changes the booth doesn't have yet.
    parent_manifest_hash = models.CharField(max_length=64, blank=True)
    parent_checked_at = models.DateTimeField(null=True, blank=True)
    parent_check_error = models.TextField(blank=True)
    agent_seen_at = models.DateTimeField(null=True, blank=True)

    @property
    def production_has_newer_changes(self):
        return bool(self.parent_manifest_hash) and (
            self.parent_manifest_hash != self.applied_manifest_hash
        )

    @property
    def agent_alive(self):
        return self.agent_seen_at is not None and (
            timezone.now() - self.agent_seen_at
            <= timedelta(seconds=AGENT_ALIVE_SECONDS)
        )

    @classmethod
    def get(cls):
        state, _ = cls.objects.get_or_create(pk=1)
        return state

    @classmethod
    def record_agent_heartbeat(cls):
        cls.objects.update_or_create(pk=1, defaults={"agent_seen_at": timezone.now()})
