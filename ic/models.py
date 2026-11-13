"""International Cinema (#246): programs, the media pipeline, and booth sync.

Production-side tables: ICProgram, ICProgramItem, ICUpload, MediaJob, ICInstance,
ICInstanceFile. ICProgram and ICProgramItem are also mirrored to the booth.

Booth-local tables (written only by the booth's sync agent, never mirrored): ICSyncRun,
ICLocalFile, ICBoothState.
"""

from datetime import timedelta
import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Content
from core.models import Resource
from core.models import ResourceFile


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
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.start_date} – {self.end_date})"

    @classmethod
    def active(cls, on=None):
        on = on or timezone.localdate()
        return cls.objects.filter(start_date__lte=on, end_date__gte=on)

    @classmethod
    def syncable(cls, on=None):
        """Active and upcoming programs, plus yesterday's, so a late show is not pulled."""
        on = on or timezone.localdate()
        return cls.objects.filter(end_date__gte=on - timedelta(days=1))

    def is_active(self, on=None):
        on = on or timezone.localdate()
        return self.start_date <= on <= self.end_date

    def is_upcoming(self, on=None):
        on = on or timezone.localdate()
        return self.start_date > on

    def is_past(self, on=None):
        on = on or timezone.localdate()
        return self.end_date < on

    def can_be_edited_by(self, user):
        return user.is_ic_editor

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

    def can_be_previewed_by(self, user):
        return user.is_ic_editor

    def subtitle_track(self):
        if self.subtitles != self.Subtitles.VTT:
            return None
        return self.content.default_subtitle_track


class MediaJob(models.Model):
    """Background work on production, run by `manage.py process_media_jobs`."""

    class Kind(models.TextChoices):
        INGEST = "ingest", "Ingest uploaded master"
        TRANSCODE = "transcode", "Transcode SD editing file"
        RELEASE_MASTER = "release_master", "Release production's copy of a master"
        CLEANUP_UPLOADS = "cleanup_uploads", "Clean up abandoned uploads"

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
    not_before = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["not_before", "id"]

    def __str__(self):
        return f"{self.get_kind_display()} [{self.status}] #{self.pk}"

    @property
    def is_pending(self):
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)


def default_upload_id():
    return uuid.uuid4()


class ICUpload(models.Model):
    """A chunked, resumable upload of a projection master."""

    class Status(models.TextChoices):
        UPLOADING = "uploading"
        PROCESSING = "processing"
        DONE = "done"
        FAILED = "failed"
        ABANDONED = "abandoned"

    id = models.UUIDField(primary_key=True, default=default_upload_id, editable=False)
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
    received_bytes = models.PositiveBigIntegerField(default=0)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.UPLOADING
    )
    error = models.TextField(blank=True)
    job = models.ForeignKey(
        MediaJob, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
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
    def percent(self):
        if not self.expected_size:
            return 0
        return round(100 * self.received_bytes / self.expected_size, 1)


# An instance that has missed this many checks in a row counts as out of contact.
CHECK_IN_GRACE_INTERVALS = 4


def hash_token(raw_token):
    return hashlib.sha256(raw_token.encode()).hexdigest()


class ICInstance(models.Model):
    """A Y-video IC instance that mirrors production: today, the one booth.

    Sync is pull-only (IC_MODE_PLAN.md section 7.1): instances check for changes every
    IC_CHECK_INTERVAL_SECONDS and report what they did. Production never connects to an
    instance and can't make one sync.
    """

    name = models.CharField(max_length=100, unique=True)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    last_seen_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_status = models.JSONField(default=dict, blank=True, editable=False)
    last_applied_manifest_hash = models.CharField(
        max_length=64, blank=True, editable=False
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "IC instance"

    def __str__(self):
        return self.name

    def issue_token(self):
        """Set a new token and return it. Only its hash is stored."""
        raw = secrets.token_urlsafe(32)
        self.token_hash = hash_token(raw)
        return raw

    @classmethod
    def for_token(cls, raw_token):
        if not raw_token:
            return None
        return cls.objects.filter(token_hash=hash_token(raw_token)).first()

    def checked_in_recently(self, now=None):
        """Whether the instance has called production within a few check intervals."""
        if self.last_seen_at is None:
            return False
        now = now or timezone.now()
        return now - self.last_seen_at <= timedelta(
            seconds=settings.IC_CHECK_INTERVAL_SECONDS * CHECK_IN_GRACE_INTERVALS
        )


class ICInstanceFile(models.Model):
    """An instance's report that it holds a verified copy of a file."""

    instance = models.ForeignKey(
        ICInstance, on_delete=models.CASCADE, related_name="files"
    )
    resource_file = models.ForeignKey(
        ResourceFile, on_delete=models.CASCADE, related_name="ic_instance_copies"
    )
    xxh64 = models.CharField(max_length=16)
    verified_at = models.DateTimeField()
    # True when the booth hashed the downloaded bytes in the reporting run, rather than
    # repeating an earlier verification. Releasing a master requires one such report.
    hashed_from_bytes = models.BooleanField(default=False)

    class Meta:
        unique_together = ("instance", "resource_file")

    def matches(self):
        return self.xxh64 == self.resource_file.checksum


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
        UP_TO_DATE = "up_to_date", "Up to date"

    FINAL_STATUSES = (
        Status.SUCCEEDED,
        Status.PARTIAL,
        Status.FAILED,
        Status.UP_TO_DATE,
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trigger = models.CharField(max_length=10, choices=Trigger.choices)
    requested_by = models.CharField(max_length=255, blank=True)
    options = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED
    )
    phase = models.CharField(max_length=20, blank=True)
    bytes_done = models.BigIntegerField(default=0)
    bytes_total = models.BigIntegerField(default=0)
    files_done = models.IntegerField(default=0)
    files_total = models.IntegerField(default=0)
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
    def is_forced(self):
        return self.trigger == self.Trigger.FORCE_PULL

    @property
    def is_final(self):
        return self.status in self.FINAL_STATUSES

    def progress_json(self):
        return {
            "run_id": str(self.pk),
            "trigger": self.trigger,
            "requested_by": self.requested_by,
            "status": self.status,
            "phase": self.phase,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "files_done": self.files_done,
            "files_total": self.files_total,
            "manifest_hash": self.manifest_hash,
            "report": self.report,
            "error": self.error,
            "requested_at": self.requested_at.isoformat()
            if self.requested_at
            else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


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


class ICBoothState(models.Model):
    """Singleton: what the booth last applied, for indicators and skip-if-unchanged."""

    applied_manifest_hash = models.CharField(max_length=64, blank=True)
    applied_fixture_sha256 = models.CharField(max_length=64, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_manifest = models.JSONField(default=dict, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(max_length=20, blank=True)
    last_run_error = models.TextField(blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_full_rehash_at = models.DateTimeField(null=True, blank=True)
    # What production's manifest hash was at the agent's last check, so the controller
    # can say whether production has changes the booth doesn't have yet.
    parent_manifest_hash = models.CharField(max_length=64, blank=True)
    parent_checked_at = models.DateTimeField(null=True, blank=True)
    parent_check_error = models.TextField(blank=True)

    @property
    def production_has_newer_changes(self):
        return bool(self.parent_manifest_hash) and (
            self.parent_manifest_hash != self.applied_manifest_hash
        )

    @classmethod
    def get(cls):
        state, _ = cls.objects.get_or_create(pk=1)
        return state
