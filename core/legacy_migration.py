import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class LegacyMigrationKind(models.TextChoices):
    COLLECTION = "collection", "Collection"
    RESOURCE = "resource", "Resource"


class LegacyMigrationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    PREFLIGHT_FAILED = "preflight_failed", "Preflight Failed"
    NEEDS_REVIEW = "needs_review", "Needs Review"
    APPROVED = "approved", "Approved"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"


class LegacyMigrationJobType(models.TextChoices):
    PREFLIGHT = "preflight", "Preflight"
    IMPORT = "import", "Import"


class LegacyMigrationJobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"


class LegacyMigrationIssueSeverity(models.TextChoices):
    BLOCKING = "blocking", "Blocking"
    WARNING = "warning", "Warning"


class LegacyMigrationFileAction(models.TextChoices):
    IMPORT = "import", "Import"
    REUSE_EXISTING = "reuse_existing", "Reuse Existing"
    SKIP = "skip", "Skip"


class LegacyMigrationUserResolutionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    AUTO = "auto", "Auto"
    MANUAL = "manual", "Manual"
    SKIP = "skip", "Skip"


class LegacyMigrationRequest(models.Model):
    request_uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="legacy_migration_requests_created",
        null=True,
        blank=True,
    )
    target_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="legacy_migration_requests_targeted",
        null=True,
        blank=True,
    )
    migration_kind = models.CharField(
        max_length=20,
        choices=LegacyMigrationKind.choices,
    )
    legacy_reference = models.CharField(max_length=500)
    legacy_identifier = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=30,
        choices=LegacyMigrationStatus.choices,
        default=LegacyMigrationStatus.DRAFT,
    )
    request_notes = models.TextField(blank=True)
    admin_notes = models.TextField(blank=True)
    target_collection_name = models.CharField(max_length=255, blank=True)
    target_collection_published = models.BooleanField(null=True, blank=True)
    target_collection_archived = models.BooleanField(null=True, blank=True)
    target_collection_public = models.BooleanField(null=True, blank=True)
    raw_snapshot = models.JSONField(default=dict, blank=True)
    preflight_completed_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)
    latest_job_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_migration_kind_display()} migration {self.request_uuid}"

    def blocking_issues(self):
        return self.issues.filter(severity=LegacyMigrationIssueSeverity.BLOCKING)

    def has_blocking_issues(self):
        return self.blocking_issues().exists()

    def queue_job(self, job_type):
        return LegacyMigrationJob.objects.create(
            request=self,
            job_type=job_type,
            status=LegacyMigrationJobStatus.QUEUED,
        )


class LegacyMigrationResource(models.Model):
    request = models.ForeignKey(
        LegacyMigrationRequest,
        on_delete=models.CASCADE,
        related_name="migration_resources",
    )
    legacy_resource_id = models.CharField(max_length=100)
    legacy_name = models.CharField(max_length=255)
    legacy_media_type = models.CharField(max_length=20, blank=True)
    legacy_owner_username = models.CharField(max_length=255, blank=True)
    legacy_owner_email = models.CharField(max_length=255, blank=True)
    legacy_owner_byu_id = models.CharField(max_length=50, blank=True)
    target_resource_name = models.CharField(max_length=255, blank=True)
    include = models.BooleanField(default=True)
    is_synthetic = models.BooleanField(default=False)
    provenance = models.JSONField(default=dict, blank=True)
    fuzzy_matches = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["legacy_name", "legacy_resource_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["request", "legacy_resource_id"],
                name="legacy_migration_resource_request_unique",
            )
        ]

    def __str__(self):
        return self.target_resource_name or self.legacy_name


class LegacyMigrationFileDecision(models.Model):
    request = models.ForeignKey(
        LegacyMigrationRequest,
        on_delete=models.CASCADE,
        related_name="file_decisions",
    )
    migration_resource = models.ForeignKey(
        LegacyMigrationResource,
        on_delete=models.CASCADE,
        related_name="file_decisions",
    )
    legacy_file_id = models.CharField(max_length=100)
    legacy_version = models.CharField(max_length=255)
    target_version = models.CharField(max_length=255, blank=True)
    legacy_path = models.CharField(max_length=1000)
    legacy_extension = models.CharField(max_length=50, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    device = models.BigIntegerField(null=True, blank=True)
    inode = models.BigIntegerField(null=True, blank=True)
    mtime_at = models.DateTimeField(null=True, blank=True)
    atime_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    linked_contents = models.JSONField(default=list, blank=True)
    linked_collections = models.JSONField(default=list, blank=True)
    linked_instructors = models.JSONField(default=list, blank=True)
    candidate_matches = models.JSONField(default=list, blank=True)
    checksum = models.CharField(max_length=16, blank=True)
    action = models.CharField(
        max_length=20,
        choices=LegacyMigrationFileAction.choices,
        default=LegacyMigrationFileAction.IMPORT,
    )
    selected_existing_resource_file = models.ForeignKey(
        "ResourceFile",
        on_delete=models.SET_NULL,
        related_name="legacy_migration_reuse_decisions",
        null=True,
        blank=True,
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["migration_resource__legacy_name", "legacy_version", "legacy_path"]
        constraints = [
            models.UniqueConstraint(
                fields=["request", "legacy_file_id"],
                name="legacy_migration_file_request_unique",
            )
        ]

    def __str__(self):
        return f"{self.legacy_version} | {self.legacy_path}"


class LegacyMigrationUserResolution(models.Model):
    request = models.ForeignKey(
        LegacyMigrationRequest,
        on_delete=models.CASCADE,
        related_name="user_resolutions",
    )
    fingerprint = models.CharField(max_length=255)
    legacy_user_id = models.CharField(max_length=100, blank=True)
    legacy_username = models.CharField(max_length=255, blank=True)
    legacy_byu_id = models.CharField(max_length=50, blank=True)
    legacy_email = models.CharField(max_length=255, blank=True)
    roles = models.JSONField(default=list, blank=True)
    contexts = models.JSONField(default=list, blank=True)
    matched_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="legacy_migration_user_resolutions",
        null=True,
        blank=True,
    )
    resolution_status = models.CharField(
        max_length=20,
        choices=LegacyMigrationUserResolutionStatus.choices,
        default=LegacyMigrationUserResolutionStatus.PENDING,
    )
    is_required = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    last_synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["legacy_username", "legacy_email", "legacy_byu_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["request", "fingerprint"],
                name="legacy_migration_user_resolution_request_unique",
            )
        ]

    def __str__(self):
        identity = self.legacy_username or self.legacy_email or self.legacy_byu_id
        return identity or self.fingerprint

    def is_resolved(self):
        return self.resolution_status in {
            LegacyMigrationUserResolutionStatus.AUTO,
            LegacyMigrationUserResolutionStatus.MANUAL,
            LegacyMigrationUserResolutionStatus.SKIP,
        }


class LegacyMigrationIssue(models.Model):
    request = models.ForeignKey(
        LegacyMigrationRequest,
        on_delete=models.CASCADE,
        related_name="issues",
    )
    migration_resource = models.ForeignKey(
        LegacyMigrationResource,
        on_delete=models.CASCADE,
        related_name="issues",
        null=True,
        blank=True,
    )
    file_decision = models.ForeignKey(
        LegacyMigrationFileDecision,
        on_delete=models.CASCADE,
        related_name="issues",
        null=True,
        blank=True,
    )
    severity = models.CharField(
        max_length=20,
        choices=LegacyMigrationIssueSeverity.choices,
    )
    code = models.CharField(max_length=100)
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="legacy_migration_issues_resolved",
        null=True,
        blank=True,
    )
    resolution_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["severity", "code", "created_at"]

    def __str__(self):
        return f"{self.severity}: {self.code}"

    def mark_resolved(self, user=None, notes=""):
        self.resolved_at = timezone.now()
        self.resolved_by = user
        self.resolution_notes = notes
        self.save(update_fields=["resolved_at", "resolved_by", "resolution_notes"])


class LegacyMigrationJob(models.Model):
    request = models.ForeignKey(
        LegacyMigrationRequest,
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    job_type = models.CharField(
        max_length=20,
        choices=LegacyMigrationJobType.choices,
    )
    status = models.CharField(
        max_length=20,
        choices=LegacyMigrationJobStatus.choices,
        default=LegacyMigrationJobStatus.QUEUED,
    )
    current_phase = models.CharField(max_length=100, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    log = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.job_type} | {self.status} | {self.request_id}"


class LegacySourceMap(models.Model):
    source_system = models.CharField(max_length=50, default="legacy")
    source_type = models.CharField(max_length=50)
    source_id = models.CharField(max_length=100)
    request = models.ForeignKey(
        LegacyMigrationRequest,
        on_delete=models.SET_NULL,
        related_name="source_maps",
        null=True,
        blank=True,
    )
    target_model = models.CharField(max_length=100)
    target_id = models.BigIntegerField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["source_type", "source_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "source_type", "source_id"],
                name="legacy_source_map_source_unique",
            )
        ]

    def __str__(self):
        return f"{self.source_type}:{self.source_id} -> {self.target_model}:{self.target_id}"
