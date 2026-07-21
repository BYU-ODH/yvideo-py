from collections import Counter
import datetime
import json
import logging
import os
from pathlib import Path
import shutil

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover - fallback only matters if dependency is missing
    from difflib import SequenceMatcher

    class _FuzzFallback:
        @staticmethod
        def WRatio(left, right):
            return int(SequenceMatcher(None, left, right).ratio() * 100)

    fuzz = _FuzzFallback()

from ..models import AnnotationSet
from ..models import BlankAnnotation
from ..models import BlurAnnotation
from ..models import BlurAnnotationPosition
from ..models import Clip
from ..models import Collection
from ..models import CollectionRole
from ..models import CollectionUserAccess
from ..models import CommentAnnotation
from ..models import Content
from ..models import Course
from ..models import Language
from ..models import MuteAnnotation
from ..models import PauseAnnotation
from ..models import Resource
from ..models import ResourceAccess
from ..models import ResourceFile
from ..models import SkipAnnotation
from ..models import Subtitle
from ..models import Track
from ..models import User
from .catalog import LegacyCatalogClient
from .dump import run_legacy_dump
from .file_index import ChecksumCache
from .file_index import CurrentFileIndex
from .file_index import compute_checksum
from .models import LegacyMigrationFileAction
from .models import LegacyMigrationFileDecision
from .models import LegacyMigrationIssue
from .models import LegacyMigrationIssueSeverity
from .models import LegacyMigrationJob
from .models import LegacyMigrationJobStatus
from .models import LegacyMigrationJobType
from .models import LegacyMigrationKind
from .models import LegacyMigrationResource
from .models import LegacyMigrationStatus
from .models import LegacyMigrationUserResolution
from .models import LegacyMigrationUserResolutionStatus
from .models import LegacySourceMap
from .parsers import LegacyFileInfo
from .parsers import build_subtitle_vtt
from .parsers import build_user_fingerprint
from .parsers import make_json_safe
from .parsers import map_legacy_media_type
from .parsers import normalize_name
from .parsers import parse_legacy_annotations
from .parsers import parse_legacy_clips
from .parsers import parse_legacy_reference
from .remote_files import get_legacy_file_extension
from .remote_files import inspect_remote_legacy_file
from .remote_files import is_remote_legacy_path
from .remote_files import resolve_legacy_file_path
from .remote_files import scp_remote_legacy_file

logger = logging.getLogger(__name__)


class LegacyMigrationJobCanceled(Exception):
    """Raised inside a running job when it was canceled from the admin."""


class LegacyMigrationService:
    def __init__(self, catalog_client=None, require_catalog=True):
        self.catalog_client = catalog_client
        if self.catalog_client is None and require_catalog:
            self.catalog_client = self._build_catalog_client()
        self.checksum_cache = ChecksumCache()
        self._current_file_index = None

    @property
    def current_file_index(self):
        # Built lazily: loading the index stats every current media file, which
        # only preflight needs. Admin saves construct this service too.
        if self._current_file_index is None:
            self._current_file_index = CurrentFileIndex(self.checksum_cache)
        return self._current_file_index

    def _get_catalog_client(self):
        if self.catalog_client is None:
            self.catalog_client = self._build_catalog_client()
        return self.catalog_client

    def _build_catalog_client(self):
        # Always re-dump before reading the legacy catalog, so preflight can
        # never see stale data. The dump only takes a couple of seconds.
        run_legacy_dump()
        return LegacyCatalogClient()

    def _migration_resource_is_included(self, request_obj, legacy_resource_id):
        migration_resource = request_obj.migration_resources.filter(
            legacy_resource_id=legacy_resource_id
        ).first()
        if not migration_resource:
            return True
        return migration_resource.include

    def _content_is_included(self, request_obj, content_row):
        legacy_resource_id = content_row.get("resource_id", "")
        if not legacy_resource_id:
            return True
        return self._migration_resource_is_included(request_obj, legacy_resource_id)

    def _coerce_created_user_result(self, created_user_result, byu_id):
        if isinstance(created_user_result, User):
            return created_user_result
        if not isinstance(created_user_result, dict):
            return None

        created_user_payload = created_user_result.get("user")
        if isinstance(created_user_payload, User):
            return created_user_payload
        if not isinstance(created_user_payload, dict):
            return None

        payload_byu_id = (created_user_payload.get("byuid") or byu_id or "").strip()
        payload_netid = (created_user_payload.get("netid") or "").strip()

        if payload_byu_id:
            user = User.objects.filter(username=payload_byu_id).first()
            if user:
                return user
        if payload_netid:
            return User.objects.filter(netid=payload_netid).first()
        return None

    def _resolve_user(self, legacy_user_dict):
        byu_id = legacy_user_dict.get("legacy_byu_id", "").strip()
        username = legacy_user_dict.get("legacy_username", "").strip()
        email = legacy_user_dict.get("legacy_email", "").strip().lower()

        if byu_id:
            user = User.objects.filter(username=byu_id).first()
            if user:
                return user, LegacyMigrationUserResolutionStatus.AUTO

        if username:
            user = User.objects.filter(netid=username).first()
            if user:
                return user, LegacyMigrationUserResolutionStatus.AUTO

        if email:
            user = User.objects.filter(email__iexact=email).first()
            if user:
                return user, LegacyMigrationUserResolutionStatus.AUTO

        if byu_id and getattr(settings, "LEGACY_MIGRATION_CREATE_MISSING_USERS", False):
            try:
                from yvideo.odhOIDCAuthenticationBackend import OIDCUserAuth

                from ..model_utils import update_user_enrollment

                created_user = self._coerce_created_user_result(
                    OIDCUserAuth().create_user({"byu_id": byu_id}),
                    byu_id,
                )
                if created_user:
                    update_user_enrollment(created_user)
                    return created_user, LegacyMigrationUserResolutionStatus.AUTO
            except Exception:
                logger.exception("Failed to auto-create user for legacy migration.")

        return None, LegacyMigrationUserResolutionStatus.PENDING

    def _upsert_user_resolution(self, request_obj, legacy_user_dict, role, context):
        fingerprint = build_user_fingerprint(legacy_user_dict)
        resolution, _ = LegacyMigrationUserResolution.objects.get_or_create(
            request=request_obj,
            fingerprint=fingerprint,
            defaults={
                "legacy_user_id": legacy_user_dict.get("legacy_user_id", ""),
                "legacy_username": legacy_user_dict.get("legacy_username", ""),
                "legacy_byu_id": legacy_user_dict.get("legacy_byu_id", ""),
                "legacy_email": legacy_user_dict.get("legacy_email", ""),
            },
        )
        matched_user, resolution_status = self._resolve_user(legacy_user_dict)
        roles = list(resolution.roles)
        if role not in roles:
            roles.append(role)
        contexts = list(resolution.contexts)
        if context not in contexts:
            contexts.append(context)
        resolution.roles = roles
        resolution.contexts = contexts
        if resolution.resolution_status == LegacyMigrationUserResolutionStatus.PENDING:
            resolution.matched_user = matched_user
            resolution.resolution_status = resolution_status
        resolution.save()
        return resolution

    def _build_fuzzy_matches(self, legacy_name):
        matches = []
        normalized_legacy_name = normalize_name(legacy_name)
        for resource in Resource.objects.all().only("id", "name"):
            normalized_candidate = normalize_name(resource.name)
            score = fuzz.WRatio(normalized_legacy_name, normalized_candidate)
            matches.append(
                {
                    "resource_id": resource.pk,
                    "resource_name": resource.name,
                    "normalized_name": normalized_candidate,
                    "score": score,
                }
            )
        matches.sort(key=lambda item: item["score"], reverse=True)
        return matches[:5]

    def _auto_reuse_checksum_match(self, candidate_matches):
        checksum_matches = [
            candidate
            for candidate in candidate_matches
            if candidate.get("match_reason") == "same_checksum"
        ]
        if len(checksum_matches) != 1:
            return {}
        return {
            "action": LegacyMigrationFileAction.REUSE_EXISTING,
            "selected_existing_resource_file_id": checksum_matches[0][
                "resource_file_id"
            ],
        }

    def _auto_reuse_resource_ids(self, pending_file_decisions):
        resource_ids = set()
        for pending_file_decision in pending_file_decisions:
            resource_file_id = pending_file_decision.get(
                "selected_existing_resource_file_id"
            )
            if not resource_file_id:
                continue
            entry = self.current_file_index.get_entry(resource_file_id)
            if entry:
                resource_ids.add(entry["resource_id"])
        return resource_ids

    def _reused_resource_ids_for_migration_resource(self, migration_resource):
        return sorted(
            {
                file_decision.selected_existing_resource_file.resource_id
                for file_decision in migration_resource.file_decisions.filter(
                    action=LegacyMigrationFileAction.REUSE_EXISTING,
                    selected_existing_resource_file__isnull=False,
                ).select_related("selected_existing_resource_file__resource")
            }
        )

    def sync_resource_reuse_targets(self, request_obj):
        for migration_resource in request_obj.migration_resources.all():
            reused_resource_ids = self._reused_resource_ids_for_migration_resource(
                migration_resource
            )
            if len(reused_resource_ids) == 1:
                target_resource_id = reused_resource_ids[0]
                if (
                    migration_resource.selected_existing_resource_id
                    != target_resource_id
                ):
                    migration_resource.selected_existing_resource_id = (
                        target_resource_id
                    )
                    migration_resource.save(
                        update_fields=["selected_existing_resource", "updated_at"]
                    )
        return request_obj

    def _get_legacy_file_info(self, file_row):
        absolute_path = resolve_legacy_file_path(file_row["filepath"])
        file_info = LegacyFileInfo(
            absolute_path=absolute_path,
            extension=get_legacy_file_extension(absolute_path),
        )
        try:
            if is_remote_legacy_path(absolute_path):
                for key, value in inspect_remote_legacy_file(absolute_path).items():
                    setattr(file_info, key, value)
            else:
                absolute_path_obj = Path(absolute_path)
                stat_result = absolute_path_obj.stat()
                file_info.realpath = str(absolute_path_obj.resolve())
                file_info.size_bytes = int(stat_result.st_size)
                file_info.device = int(stat_result.st_dev)
                file_info.inode = int(stat_result.st_ino)
                file_info.mtime_ns = int(
                    getattr(
                        stat_result,
                        "st_mtime_ns",
                        int(stat_result.st_mtime * 1_000_000_000),
                    )
                )
                file_info.mtime_at = datetime.datetime.fromtimestamp(
                    stat_result.st_mtime, tz=datetime.UTC
                )
                file_info.atime_at = datetime.datetime.fromtimestamp(
                    stat_result.st_atime, tz=datetime.UTC
                )
        except OSError as exc:
            file_info.inspection_error = str(exc)
            logger.warning(
                "Legacy file inspection failed for %s: %s",
                absolute_path,
                file_info.inspection_error,
            )
        return file_info

    def _inspect_snapshot_files(self, snapshot):
        """Stat legacy files and compute candidate matches for every file in
        the snapshot. This is the slow part of preflight (local/remote stat
        calls plus checksums), so it runs before any database transaction."""
        inspections = {}
        for resource_payload in snapshot.get("resources", []):
            for file_row in resource_payload.get("files", []):
                file_info = self._get_legacy_file_info(file_row)
                inspections[file_row["id"]] = {
                    "file_info": file_info,
                    "candidate_matches": self.current_file_index.find_candidates(
                        file_info
                    ),
                    "checksum": "",
                }

        size_counts = Counter(
            entry["file_info"].size_bytes
            for entry in inspections.values()
            if entry["file_info"].size_bytes is not None
        )
        for entry in inspections.values():
            file_info = entry["file_info"]
            if file_info.size_bytes is None:
                continue
            has_checksum_candidate = any(
                candidate["match_reason"] == "same_checksum"
                for candidate in entry["candidate_matches"]
            )
            # Only checksum files that could be duplicates (of a current file
            # or of another file in this request) to avoid hashing everything.
            if has_checksum_candidate or size_counts[file_info.size_bytes] > 1:
                entry["checksum"] = self.checksum_cache.get_or_compute_legacy_checksum(
                    file_info
                )
        return inspections

    def preflight_request(self, request_obj):
        migration_kind, legacy_identifier = parse_legacy_reference(
            request_obj.legacy_reference,
            requested_kind=request_obj.migration_kind,
        )

        if migration_kind == LegacyMigrationKind.COLLECTION:
            snapshot = self._get_catalog_client().build_collection_snapshot(
                legacy_identifier
            )
        else:
            snapshot = self._get_catalog_client().build_resource_snapshot(
                legacy_identifier
            )

        file_inspections = self._inspect_snapshot_files(snapshot)

        with transaction.atomic():
            request_obj.migration_kind = migration_kind
            request_obj.legacy_identifier = legacy_identifier
            request_obj.raw_snapshot = make_json_safe(snapshot)
            request_obj.preflight_completed_at = timezone.now()
            request_obj.status = LegacyMigrationStatus.NEEDS_REVIEW
            request_obj.latest_job_error = ""
            request_obj.save()

            request_obj.migration_resources.all().delete()
            request_obj.file_decisions.all().delete()
            request_obj.user_resolutions.all().delete()
            request_obj.issues.all().delete()

            if migration_kind == LegacyMigrationKind.COLLECTION:
                collection_owner = snapshot["collection"]["owner"]
                self._upsert_user_resolution(
                    request_obj,
                    collection_owner,
                    "collection_owner",
                    f"collection:{snapshot['collection']['legacy_collection_id']}",
                )
                update_fields = []
                if not request_obj.target_owner:
                    request_obj.target_owner = request_obj.requested_by
                    update_fields.append("target_owner")
                if not request_obj.target_collection_name:
                    request_obj.target_collection_name = snapshot["collection"]["name"]
                    update_fields.append("target_collection_name")
                if update_fields:
                    request_obj.save(update_fields=[*update_fields, "updated_at"])

            for access_row in snapshot.get("collection_access", []):
                self._upsert_user_resolution(
                    request_obj,
                    {
                        "legacy_user_id": access_row["legacy_user_id"] or "",
                        "legacy_username": access_row["username"] or "",
                        "legacy_byu_id": access_row["byu_person_id"] or "",
                        "legacy_email": access_row["email"] or "",
                    },
                    f"collection_role:{access_row['account_role']}",
                    f"collection:{access_row['collection_id']}",
                )

            for resource_payload in snapshot.get("resources", []):
                resource_row = LegacyMigrationResource.objects.create(
                    request=request_obj,
                    legacy_resource_id=resource_payload["legacy_resource_id"],
                    legacy_name=resource_payload["name"],
                    legacy_media_type=resource_payload.get("resource_type", ""),
                    target_resource_name=resource_payload["name"],
                    is_synthetic=resource_payload["legacy_resource_id"].startswith(
                        "synthetic:"
                    ),
                    fuzzy_matches=self._build_fuzzy_matches(resource_payload["name"]),
                )

                for access_row in resource_payload.get("resource_access", []):
                    self._upsert_user_resolution(
                        request_obj,
                        {
                            "legacy_user_id": access_row["legacy_user_id"] or "",
                            "legacy_username": access_row["username"] or "",
                            "legacy_byu_id": access_row["byu_person_id"] or "",
                            "legacy_email": access_row["email"] or "",
                        },
                        "resource_access",
                        f"resource:{resource_payload['legacy_resource_id']}",
                    )

                pending_file_decisions = []
                for file_row in resource_payload.get("files", []):
                    inspection = file_inspections[file_row["id"]]
                    file_info = inspection["file_info"]
                    candidate_matches = inspection["candidate_matches"]
                    auto_reuse_defaults = self._auto_reuse_checksum_match(
                        candidate_matches
                    )
                    pending_file_decisions.append(
                        {
                            "request": request_obj,
                            "migration_resource": resource_row,
                            "legacy_file_id": file_row["id"],
                            "legacy_version": file_row["file_version"] or "",
                            "target_version": file_row["file_version"] or "",
                            "legacy_path": file_row["filepath"],
                            "legacy_extension": file_info.extension,
                            "size_bytes": file_info.size_bytes,
                            "device": file_info.device,
                            "inode": file_info.inode,
                            "mtime_at": file_info.mtime_at,
                            "atime_at": file_info.atime_at,
                            "checksum": inspection["checksum"],
                            "metadata": {
                                "legacy_metadata": file_row["metadata"] or "",
                                "absolute_path": file_info.absolute_path,
                                "realpath": file_info.realpath,
                                "mtime_ns": file_info.mtime_ns,
                                "inspection_error": file_info.inspection_error,
                            },
                            "candidate_matches": candidate_matches,
                            **auto_reuse_defaults,
                        }
                    )

                if len(self._auto_reuse_resource_ids(pending_file_decisions)) > 1:
                    for pending_file_decision in pending_file_decisions:
                        pending_file_decision.pop("action", None)
                        pending_file_decision.pop(
                            "selected_existing_resource_file_id", None
                        )

                for pending_file_decision in pending_file_decisions:
                    LegacyMigrationFileDecision.objects.create(**pending_file_decision)

            self.sync_resource_reuse_targets(request_obj)
            self.sync_request_issues(request_obj)
            request_obj.status = LegacyMigrationStatus.NEEDS_REVIEW
            request_obj.save(update_fields=["status", "updated_at"])
            return request_obj

    def _resolve_collection_role(self, raw_role):
        try:
            return CollectionRole(int(raw_role))
        except (TypeError, ValueError):
            return None

    def _duplicate_import_groups(self, file_decisions):
        """Group to-be-imported decisions that point at identical file content,
        using data recorded during preflight (no file I/O here)."""
        groups = {}
        for file_decision in file_decisions:
            if not file_decision.migration_resource.include:
                continue
            if file_decision.action != LegacyMigrationFileAction.IMPORT:
                continue
            if file_decision.checksum:
                key = ("checksum", file_decision.checksum)
            elif file_decision.device is not None and file_decision.inode is not None:
                key = ("inode", file_decision.device, file_decision.inode)
            elif file_decision.metadata.get("realpath"):
                key = ("realpath", file_decision.metadata["realpath"])
            else:
                continue
            groups.setdefault(key, []).append(file_decision)
        return [group for group in groups.values() if len(group) > 1]

    def sync_request_issues(self, request_obj):
        self.sync_resource_reuse_targets(request_obj)
        request_obj.issues.all().delete()

        for resolution in request_obj.user_resolutions.all():
            if resolution.is_required and not resolution.is_resolved():
                LegacyMigrationIssue.objects.create(
                    request=request_obj,
                    severity=LegacyMigrationIssueSeverity.BLOCKING,
                    code="unresolved_user",
                    message=(
                        "A required legacy user could not be mapped automatically: "
                        f"{resolution.legacy_username or resolution.legacy_email or resolution.legacy_byu_id}"
                    ),
                    details={"fingerprint": resolution.fingerprint},
                )

        if (
            request_obj.migration_kind == LegacyMigrationKind.COLLECTION
            and request_obj.target_owner
            and request_obj.target_collection_name
        ):
            conflict_qs = Collection.objects.filter(
                owner=request_obj.target_owner,
                name=request_obj.target_collection_name,
            )
            # A collection created by an earlier (possibly failed) run of this
            # same request is not a conflict; excluding it keeps retries viable.
            snapshot_collection = request_obj.raw_snapshot.get("collection") or {}
            legacy_collection_id = snapshot_collection.get("legacy_collection_id")
            if legacy_collection_id:
                mapped_collection = self._get_source_map_target(
                    "collection", legacy_collection_id, Collection
                )
                if mapped_collection:
                    conflict_qs = conflict_qs.exclude(pk=mapped_collection.pk)
            if conflict_qs.exists():
                LegacyMigrationIssue.objects.create(
                    request=request_obj,
                    severity=LegacyMigrationIssueSeverity.BLOCKING,
                    code="collection_name_conflict",
                    message="The target owner already has a collection with the selected name.",
                    details={
                        "target_collection_name": request_obj.target_collection_name
                    },
                )

        for migration_resource in request_obj.migration_resources.all():
            if not migration_resource.include:
                continue
            fuzzy_matches = migration_resource.fuzzy_matches or []
            if fuzzy_matches and not migration_resource.selected_existing_resource_id:
                top_match = fuzzy_matches[0]
                if (
                    top_match["score"] >= 92
                    or normalize_name(migration_resource.target_resource_name)
                    == top_match["normalized_name"]
                ):
                    severity = LegacyMigrationIssueSeverity.BLOCKING
                elif top_match["score"] >= 80:
                    severity = LegacyMigrationIssueSeverity.WARNING
                else:
                    severity = None

                if severity:
                    LegacyMigrationIssue.objects.create(
                        request=request_obj,
                        migration_resource=migration_resource,
                        severity=severity,
                        code="similar_resource_name",
                        message=(
                            "A similar resource already exists in the new database: "
                            f"{top_match['resource_name']} ({top_match['score']})"
                        ),
                        details=top_match,
                    )

            reuse_resource_ids = set(
                self._reused_resource_ids_for_migration_resource(migration_resource)
            )
            if len(reuse_resource_ids) > 1:
                LegacyMigrationIssue.objects.create(
                    request=request_obj,
                    migration_resource=migration_resource,
                    severity=LegacyMigrationIssueSeverity.BLOCKING,
                    code="reuse_conflict",
                    message=(
                        "This resource is set to reuse files from multiple existing resources."
                    ),
                    details={"resource_ids": sorted(reuse_resource_ids)},
                )

        file_decisions = list(
            request_obj.file_decisions.select_related(
                "migration_resource",
                "selected_existing_resource_file__resource",
            )
        )
        for file_decision in file_decisions:
            if not file_decision.migration_resource.include:
                continue
            if file_decision.size_bytes is None:
                issue_details = {"path": file_decision.legacy_path}
                inspection_error = file_decision.metadata.get("inspection_error", "")
                if inspection_error:
                    issue_details["inspection_error"] = inspection_error
                LegacyMigrationIssue.objects.create(
                    request=request_obj,
                    migration_resource=file_decision.migration_resource,
                    file_decision=file_decision,
                    severity=LegacyMigrationIssueSeverity.BLOCKING,
                    code="missing_legacy_file",
                    message=(
                        "The legacy file could not be inspected during preflight. "
                        "See details for the failing command."
                    ),
                    details=issue_details,
                )

            if (
                file_decision.action == LegacyMigrationFileAction.REUSE_EXISTING
                and not file_decision.selected_existing_resource_file_id
            ):
                LegacyMigrationIssue.objects.create(
                    request=request_obj,
                    migration_resource=file_decision.migration_resource,
                    file_decision=file_decision,
                    severity=LegacyMigrationIssueSeverity.BLOCKING,
                    code="reuse_missing_target",
                    message="Reuse was selected, but no existing resource file was chosen.",
                )

            exact_match_candidates = list(file_decision.candidate_matches)
            if (
                exact_match_candidates
                and file_decision.action == LegacyMigrationFileAction.IMPORT
            ):
                LegacyMigrationIssue.objects.create(
                    request=request_obj,
                    migration_resource=file_decision.migration_resource,
                    file_decision=file_decision,
                    severity=LegacyMigrationIssueSeverity.BLOCKING,
                    code="duplicate_file_requires_decision",
                    message=(
                        "An exact duplicate file already exists. Choose reuse existing, skip, "
                        "or change the plan before importing."
                    ),
                    details={"candidates": exact_match_candidates},
                )

            if (
                file_decision.action == LegacyMigrationFileAction.REUSE_EXISTING
                and file_decision.selected_existing_resource_file_id
            ):
                candidate_ids = {
                    candidate["resource_file_id"]
                    for candidate in file_decision.candidate_matches
                }
                if (
                    candidate_ids
                    and file_decision.selected_existing_resource_file_id
                    not in candidate_ids
                ):
                    LegacyMigrationIssue.objects.create(
                        request=request_obj,
                        migration_resource=file_decision.migration_resource,
                        file_decision=file_decision,
                        severity=LegacyMigrationIssueSeverity.BLOCKING,
                        code="reuse_target_not_candidate",
                        message=(
                            "The selected resource file is not one of the preflight candidates "
                            "for this legacy file."
                        ),
                    )

        for duplicate_group in self._duplicate_import_groups(file_decisions):
            LegacyMigrationIssue.objects.create(
                request=request_obj,
                migration_resource=duplicate_group[0].migration_resource,
                file_decision=duplicate_group[0],
                severity=LegacyMigrationIssueSeverity.WARNING,
                code="duplicate_file_in_request",
                message=(
                    "These legacy files are identical. The file will be imported "
                    "once and shared by every resource that references it."
                ),
                details={
                    "legacy_file_ids": [
                        decision.legacy_file_id for decision in duplicate_group
                    ],
                    "paths": [decision.legacy_path for decision in duplicate_group],
                },
            )

        for access_row in request_obj.raw_snapshot.get("collection_access", []):
            if self._resolve_collection_role(access_row.get("account_role")) is None:
                LegacyMigrationIssue.objects.create(
                    request=request_obj,
                    severity=LegacyMigrationIssueSeverity.WARNING,
                    code="unknown_collection_role",
                    message=(
                        "A legacy collection access row has an unrecognized "
                        f"account_role ({access_row.get('account_role')!r}) and "
                        "will be skipped during import."
                    ),
                    details={
                        "username": access_row.get("username") or "",
                        "account_role": access_row.get("account_role"),
                    },
                )

        contents = request_obj.raw_snapshot.get("contents", [])
        for content_row in contents:
            if not self._content_is_included(request_obj, content_row):
                continue
            if content_row.get("resource_id", "").startswith("synthetic:"):
                continue
            if not content_row.get("file_version"):
                continue
            matching_decisions = request_obj.file_decisions.filter(
                migration_resource__legacy_resource_id=content_row["resource_id"],
                legacy_version=content_row["file_version"],
            ).exclude(action=LegacyMigrationFileAction.SKIP)
            if not matching_decisions.exists():
                LegacyMigrationIssue.objects.create(
                    request=request_obj,
                    severity=LegacyMigrationIssueSeverity.BLOCKING,
                    code="content_missing_selected_file",
                    message=(
                        f"Content '{content_row['title']}' no longer has a selected file "
                        "to back it in the new system."
                    ),
                    details={"legacy_content_id": content_row["id"]},
                )

            for subtitle_row in content_row.get("subtitles", []):
                language = self._resolve_language(subtitle_row["language"])
                if not language:
                    LegacyMigrationIssue.objects.create(
                        request=request_obj,
                        severity=LegacyMigrationIssueSeverity.BLOCKING,
                        code="missing_subtitle_language",
                        message=(
                            f"Subtitle language '{subtitle_row['language']}' does not match "
                            "a current Language row."
                        ),
                        details={"legacy_subtitle_id": subtitle_row["id"]},
                    )

        return request_obj

    def approve_and_queue_import(self, request_obj):
        self.sync_request_issues(request_obj)
        if request_obj.has_blocking_issues():
            raise ValueError("The migration request still has blocking issues.")
        return self._queue_job(
            request_obj,
            LegacyMigrationJobType.IMPORT,
            LegacyMigrationStatus.QUEUED,
        )

    def queue_preflight(self, request_obj):
        return self._queue_job(
            request_obj,
            LegacyMigrationJobType.PREFLIGHT,
            LegacyMigrationStatus.SUBMITTED,
        )

    def _queue_job(self, request_obj, job_type, request_status):
        request_obj.status = request_status
        request_obj.save(update_fields=["status", "updated_at"])
        return request_obj.queue_job(job_type)

    def _claim_job(self, job):
        """Atomically claim a queued job. Returns False if another worker
        claimed it (or it was canceled) after we fetched it."""
        return (
            LegacyMigrationJob.objects.filter(
                pk=job.pk, status=LegacyMigrationJobStatus.QUEUED
            ).update(status=LegacyMigrationJobStatus.RUNNING)
            == 1
        )

    def recover_running_jobs(self):
        """Return jobs interrupted by a previous worker to the queue.

        The worker process holds a singleton process lock before calling this,
        so every running job belongs to a worker that is no longer alive.
        """
        recovered_jobs = []
        recovery_timestamp = timezone.now()

        with transaction.atomic():
            running_jobs = list(
                LegacyMigrationJob.objects.select_related("request")
                .filter(status=LegacyMigrationJobStatus.RUNNING)
                .order_by("created_at")
            )
            for job in running_jobs:
                previous_phase = job.current_phase
                log_entries = list(job.log)
                log_entries.append(
                    {
                        "event": "recovered",
                        "previous_phase": previous_phase,
                        "timestamp": recovery_timestamp.isoformat(),
                    }
                )
                job.status = LegacyMigrationJobStatus.QUEUED
                job.started_at = None
                job.current_phase = ""
                job.log = log_entries
                job.save(
                    update_fields=[
                        "status",
                        "started_at",
                        "current_phase",
                        "log",
                        "updated_at",
                    ]
                )

                request_obj = job.request
                request_obj.status = (
                    LegacyMigrationStatus.SUBMITTED
                    if job.job_type == LegacyMigrationJobType.PREFLIGHT
                    else LegacyMigrationStatus.QUEUED
                )
                request_obj.save(update_fields=["status", "updated_at"])
                recovered_jobs.append(
                    {
                        "job_id": job.pk,
                        "job_type": job.job_type,
                        "request_uuid": str(request_obj.request_uuid),
                        "attempts": job.attempts,
                        "previous_phase": previous_phase,
                    }
                )

        for recovered_job in recovered_jobs:
            logger.warning(
                "Recovered interrupted legacy migration job %(job_id)s "
                "(%(job_type)s) for request %(request_uuid)s; attempts=%(attempts)s, "
                "previous_phase=%(previous_phase)r. Returned job to queue.",
                recovered_job,
            )
        if recovered_jobs:
            logger.warning(
                "Recovered %s interrupted legacy migration job(s).",
                len(recovered_jobs),
            )
        else:
            logger.info("No interrupted legacy migration jobs required recovery.")

        return len(recovered_jobs)

    def run_next_job(self):
        while True:
            job = (
                LegacyMigrationJob.objects.filter(
                    status=LegacyMigrationJobStatus.QUEUED
                )
                .order_by("created_at")
                .first()
            )
            if not job:
                return None
            if not self._claim_job(job):
                continue
            self.run_job(job)
            return job

    def run_job(self, job):
        job.status = LegacyMigrationJobStatus.RUNNING
        job.started_at = timezone.now()
        job.attempts += 1
        job.save(update_fields=["status", "started_at", "attempts", "updated_at"])
        request_obj = job.request
        request_obj.status = LegacyMigrationStatus.RUNNING
        request_obj.save(update_fields=["status", "updated_at"])
        logger.info(
            "Starting legacy migration job %s (%s) for request %s; attempt=%s.",
            job.pk,
            job.job_type,
            request_obj.request_uuid,
            job.attempts,
        )

        try:
            if job.job_type == LegacyMigrationJobType.PREFLIGHT:
                self.preflight_request(request_obj)
                request_obj.status = LegacyMigrationStatus.NEEDS_REVIEW
            else:
                self.import_request(request_obj, job)
                request_obj.status = LegacyMigrationStatus.COMPLETED
                request_obj.imported_at = timezone.now()
            request_obj.latest_job_error = ""
            request_obj.save(
                update_fields=[
                    "status",
                    "imported_at",
                    "latest_job_error",
                    "updated_at",
                ]
            )
            job.status = LegacyMigrationJobStatus.COMPLETED
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "finished_at", "updated_at"])
            logger.info(
                "Completed legacy migration job %s (%s) for request %s.",
                job.pk,
                job.job_type,
                request_obj.request_uuid,
            )
        except LegacyMigrationJobCanceled:
            job.status = LegacyMigrationJobStatus.CANCELED
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "finished_at", "updated_at"])
            request_obj.status = LegacyMigrationStatus.CANCELED
            request_obj.save(update_fields=["status", "updated_at"])
            logger.warning(
                "Canceled legacy migration job %s (%s) for request %s.",
                job.pk,
                job.job_type,
                request_obj.request_uuid,
            )
        except Exception as exc:
            logger.exception(
                "Legacy migration job %s (%s) for request %s failed.",
                job.pk,
                job.job_type,
                request_obj.request_uuid,
            )
            job.status = LegacyMigrationJobStatus.FAILED
            job.last_error = str(exc)
            job.finished_at = timezone.now()
            job.save(
                update_fields=["status", "last_error", "finished_at", "updated_at"]
            )
            request_obj.status = (
                LegacyMigrationStatus.PREFLIGHT_FAILED
                if job.job_type == LegacyMigrationJobType.PREFLIGHT
                else LegacyMigrationStatus.FAILED
            )
            request_obj.latest_job_error = str(exc)
            request_obj.save(update_fields=["status", "latest_job_error", "updated_at"])
            raise

    def _log_job_phase(self, job, phase_name):
        current_status = (
            LegacyMigrationJob.objects.filter(pk=job.pk)
            .values_list("status", flat=True)
            .first()
        )
        if current_status == LegacyMigrationJobStatus.CANCELED:
            raise LegacyMigrationJobCanceled(
                f"Job {job.pk} was canceled; stopping before phase '{phase_name}'."
            )
        job.current_phase = phase_name
        log_entries = list(job.log)
        log_entries.append(
            {
                "phase": phase_name,
                "timestamp": timezone.now().isoformat(),
            }
        )
        job.log = log_entries
        job.save(update_fields=["current_phase", "log", "updated_at"])

    def _resolve_language(self, raw_language):
        language = (raw_language or "").strip()
        if not language:
            return None
        return (
            Language.objects.filter(lang_tag__iexact=language).first()
            or Language.objects.filter(language__iexact=language).first()
        )

    def _get_target_owner(self, request_obj):
        owner = request_obj.target_owner or request_obj.requested_by
        if not owner:
            raise ValueError("A target owner is required before import.")
        return owner

    def _get_source_map_target(self, source_type, source_id, model_class):
        source_map = LegacySourceMap.objects.filter(
            source_system="legacy",
            source_type=source_type,
            source_id=source_id,
        ).first()
        if not source_map or source_map.target_model != model_class.__name__:
            return None
        return model_class.objects.filter(pk=source_map.target_id).first()

    def _upsert_source_map(
        self, request_obj, source_type, source_id, target_obj, metadata=None
    ):
        LegacySourceMap.objects.update_or_create(
            source_system="legacy",
            source_type=source_type,
            source_id=source_id,
            defaults={
                "request": request_obj,
                "target_model": target_obj.__class__.__name__,
                "target_id": target_obj.pk,
                "metadata": metadata or {},
            },
        )

    def _snapshot_resource(self, request_obj, legacy_resource_id):
        for resource_payload in request_obj.raw_snapshot.get("resources", []):
            if resource_payload.get("legacy_resource_id") == legacy_resource_id:
                return resource_payload
        return {}

    def _determine_target_resource(self, request_obj, migration_resource, owner):
        existing = self._get_source_map_target(
            "resource",
            migration_resource.legacy_resource_id,
            Resource,
        )
        if existing:
            return existing

        if migration_resource.selected_existing_resource_id:
            target_resource = migration_resource.selected_existing_resource
            self._upsert_source_map(
                request_obj,
                "resource",
                migration_resource.legacy_resource_id,
                target_resource,
            )
            return target_resource

        reused_files = list(
            migration_resource.file_decisions.filter(
                action=LegacyMigrationFileAction.REUSE_EXISTING,
                selected_existing_resource_file__isnull=False,
            ).select_related("selected_existing_resource_file__resource")
        )
        if reused_files:
            target_resource = reused_files[0].selected_existing_resource_file.resource
            self._upsert_source_map(
                request_obj,
                "resource",
                migration_resource.legacy_resource_id,
                target_resource,
            )
            return target_resource

        snapshot_resource = self._snapshot_resource(
            request_obj, migration_resource.legacy_resource_id
        )
        notes = json.dumps(
            {
                "legacy_resource_id": migration_resource.legacy_resource_id,
                "legacy": {
                    "requester_email": snapshot_resource.get("requester_email", ""),
                    "metadata": snapshot_resource.get("metadata", ""),
                    "copyrighted": bool(snapshot_resource.get("copyrighted", True)),
                    "physical_copy_exists": bool(
                        snapshot_resource.get("physical_copy_exists", False)
                    ),
                    "views": int(snapshot_resource.get("views") or 0),
                },
            },
            sort_keys=True,
        )
        target_resource = Resource.objects.create(
            name=migration_resource.target_resource_name
            or migration_resource.legacy_name,
            media_type=map_legacy_media_type(migration_resource.legacy_media_type),
            requester_username=owner.username,
            copyrighted=bool(snapshot_resource.get("copyrighted", True)),
            physical_copy_exists=bool(
                snapshot_resource.get("physical_copy_exists", False)
            ),
            views=int(snapshot_resource.get("views") or 0),
            notes=notes,
        )
        self._upsert_source_map(
            request_obj,
            "resource",
            migration_resource.legacy_resource_id,
            target_resource,
        )
        return target_resource

    def _ensure_collection(self, request_obj, owner):
        snapshot_collection = request_obj.raw_snapshot.get("collection")
        if not snapshot_collection:
            return None

        mapped_collection = self._get_source_map_target(
            "collection",
            snapshot_collection["legacy_collection_id"],
            Collection,
        )
        if mapped_collection:
            return mapped_collection

        collection = Collection.objects.create(
            name=request_obj.target_collection_name or snapshot_collection["name"],
            owner=owner,
            published=(
                request_obj.target_collection_published
                if request_obj.target_collection_published is not None
                else snapshot_collection["published"]
            ),
            archived=(
                request_obj.target_collection_archived
                if request_obj.target_collection_archived is not None
                else snapshot_collection["archived"]
            ),
            public=(
                request_obj.target_collection_public
                if request_obj.target_collection_public is not None
                else snapshot_collection["public"]
            ),
        )
        self._upsert_source_map(
            request_obj,
            "collection",
            snapshot_collection["legacy_collection_id"],
            collection,
        )
        return collection

    def _import_file_to_storage(self, source_path, resource, version):
        extension = get_legacy_file_extension(source_path)
        relative_name = f"{resource.name}/{version}{extension}"
        destination = Path(settings.MEDIA_ROOT) / relative_name
        if destination.exists():
            if ResourceFile.objects.filter(file=relative_name).exists():
                # The path belongs to another resource file; never clobber it.
                relative_name = default_storage.get_available_name(relative_name)
                destination = Path(settings.MEDIA_ROOT) / relative_name
            else:
                # Stale leftover from an earlier failed import attempt.
                destination.unlink()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if is_remote_legacy_path(source_path):
            scp_remote_legacy_file(source_path, destination)
            return relative_name
        source_path = Path(source_path)
        try:
            if source_path.stat().st_dev == destination.parent.stat().st_dev:
                os.link(source_path, destination)
            else:
                shutil.copy2(source_path, destination)
        except OSError:
            shutil.copy2(source_path, destination)
        return relative_name

    def _ensure_resource_file(self, request_obj, target_resource, file_decision):
        if file_decision.action == LegacyMigrationFileAction.SKIP:
            return None

        if (
            file_decision.action == LegacyMigrationFileAction.REUSE_EXISTING
            and file_decision.selected_existing_resource_file_id
        ):
            target_file = file_decision.selected_existing_resource_file
            self._upsert_source_map(
                request_obj,
                "file",
                file_decision.legacy_file_id,
                target_file,
            )
            return target_file

        mapped_file = self._get_source_map_target(
            "file", file_decision.legacy_file_id, ResourceFile
        )
        if mapped_file:
            return mapped_file

        relative_name = self._import_file_to_storage(
            resolve_legacy_file_path(file_decision.legacy_path),
            target_resource,
            file_decision.target_version or file_decision.legacy_version,
        )

        # ResourceFile.checksum is unique, so importing bytes that already
        # exist (e.g. the same legacy file shared by two resources in this
        # request) must reuse the existing row instead of crashing on save.
        checksum = compute_checksum(Path(settings.MEDIA_ROOT) / relative_name)
        existing_file = ResourceFile.objects.filter(checksum=checksum).first()
        if existing_file:
            (Path(settings.MEDIA_ROOT) / relative_name).unlink(missing_ok=True)
            self._upsert_source_map(
                request_obj,
                "file",
                file_decision.legacy_file_id,
                existing_file,
            )
            return existing_file

        resource_file = ResourceFile(
            resource=target_resource,
            version=file_decision.target_version or file_decision.legacy_version,
            full_video=True,
            checksum=checksum,
            checksum_at=timezone.now(),
            notes=json.dumps(
                {
                    "legacy_file_id": file_decision.legacy_file_id,
                    "legacy_path": file_decision.legacy_path,
                },
                sort_keys=True,
            ),
        )
        resource_file.file.name = relative_name
        resource_file.save()
        self._upsert_source_map(
            request_obj,
            "file",
            file_decision.legacy_file_id,
            resource_file,
        )
        return resource_file

    def _ensure_content(
        self, request_obj, collection, content_row, target_resource, target_file
    ):
        mapped_content = self._get_source_map_target(
            "content", content_row["id"], Content
        )
        defaults = {
            "collection": collection,
            "title": content_row["title"],
            "resource": target_resource,
            "resource_file": target_file,
            "url": content_row["url"],
            "description": content_row["description"] or "",
            "allow_definitions": bool(content_row["allow_definitions"]),
            "allow_notes": bool(content_row["allow_notes"]),
            "allow_captions": bool(content_row["allow_captions"]),
            "published": bool(content_row["published"]),
            "words": content_row["words"] or "",
        }
        if mapped_content:
            for field_name, value in defaults.items():
                setattr(mapped_content, field_name, value)
            mapped_content.save()
            content = mapped_content
        else:
            content = Content.objects.create(**defaults)
            self._upsert_source_map(request_obj, "content", content_row["id"], content)
        return content

    def _ensure_clip(self, request_obj, content, legacy_clip, clip_index):
        source_id = f"{content.pk}:{clip_index}"
        clip = self._get_source_map_target("clip", source_id, Clip)
        defaults = {
            "resource": content.get_resource(),
            "owner": self._get_target_owner(request_obj),
            "name": legacy_clip.get("title")
            or legacy_clip.get("label")
            or f"{content.title} Clip {clip_index + 1}",
            "start_time": float(legacy_clip.get("start", 0) or 0),
            "end_time": float(legacy_clip.get("end", 0) or 0),
            "description": legacy_clip.get("description", ""),
        }
        if clip:
            for field_name, value in defaults.items():
                setattr(clip, field_name, value)
            clip.save()
        else:
            clip = Clip.objects.create(**defaults)
            self._upsert_source_map(request_obj, "clip", source_id, clip)
        content.clips.add(clip)
        return clip

    def _annotation_model_for_type(self, legacy_type):
        normalized = (legacy_type or "").strip().lower()
        mapping = {
            "skip": SkipAnnotation,
            "mute": MuteAnnotation,
            "pause": PauseAnnotation,
            "comment": CommentAnnotation,
            "blank": BlankAnnotation,
            "censor": BlurAnnotation,
        }
        return mapping.get(normalized)

    def _import_annotations(self, request_obj, content, legacy_annotations):
        if not legacy_annotations:
            return None

        source_id = str(content.pk)
        annotation_set = self._get_source_map_target(
            "annotation_set",
            source_id,
            AnnotationSet,
        )
        if annotation_set:
            annotation_set.tracks.all().delete()
        else:
            annotation_set = AnnotationSet.objects.create(
                name=f"Imported {content.title} Annotations",
                resource=content.get_resource(),
                owner=self._get_target_owner(request_obj),
            )
            self._upsert_source_map(
                request_obj,
                "annotation_set",
                source_id,
                annotation_set,
            )

        tracks_by_layer = {}
        for index, legacy_event in enumerate(legacy_annotations):
            model_class = self._annotation_model_for_type(legacy_event.get("type"))
            if not model_class:
                continue
            layer_number = int(legacy_event.get("layer", 0) or 0)
            track = tracks_by_layer.get(layer_number)
            if track is None:
                track = Track.objects.create(
                    annotation_set=annotation_set,
                    name=f"Imported Layer {layer_number}",
                    stack_position=layer_number,
                )
                tracks_by_layer[layer_number] = track
            start_time = float(legacy_event.get("start", 0) or 0)
            end_time = float(legacy_event.get("end", start_time) or start_time)
            common_kwargs = {
                "track": track,
                "name": legacy_event.get("title")
                or legacy_event.get("label")
                or legacy_event.get("type", ""),
                "start_time": start_time,
                "end_time": end_time,
                "description": legacy_event.get("description")
                or legacy_event.get("comment")
                or legacy_event.get("type", ""),
                "active": True,
            }

            if model_class is SkipAnnotation:
                annotation = model_class.objects.create(
                    message=legacy_event.get("message", ""),
                    **common_kwargs,
                )
            elif model_class is PauseAnnotation:
                # Pause is a point marker: its end time equals its start time.
                annotation = model_class.objects.create(
                    message=legacy_event.get("message", ""),
                    **{**common_kwargs, "end_time": start_time},
                )
            elif model_class is CommentAnnotation:
                position = legacy_event.get("position") or {}
                annotation = model_class.objects.create(
                    text=legacy_event.get("comment") or legacy_event.get("text") or "",
                    top_left_x=float(position.get("x", 0) or 0),
                    top_left_y=float(position.get("y", 0) or 0),
                    **common_kwargs,
                )
            elif model_class is BlankAnnotation:
                annotation = model_class.objects.create(type="k", **common_kwargs)
            elif model_class is BlurAnnotation:
                annotation = model_class.objects.create(**common_kwargs)
                for position_values in (legacy_event.get("position") or {}).values():
                    if (
                        not isinstance(position_values, list)
                        or len(position_values) < 5
                    ):
                        continue
                    BlurAnnotationPosition.objects.create(
                        blur_annotation=annotation,
                        time=float(position_values[0] or 0),
                        x=float(position_values[1] or 0),
                        y=float(position_values[2] or 0),
                        width=float(position_values[3] or 0),
                        height=float(position_values[4] or 0),
                    )
            else:
                annotation = model_class.objects.create(**common_kwargs)

            self._upsert_source_map(
                request_obj,
                "annotation",
                f"{content.pk}:{index}",
                annotation,
            )

        content.annotation_set = annotation_set
        content.save(update_fields=["annotation_set", "updated_at"])
        return annotation_set

    def _import_subtitles(self, request_obj, content, subtitle_rows):
        imported = []
        for subtitle_row in subtitle_rows:
            language = self._resolve_language(subtitle_row["language"])
            if not language:
                raise ValueError(
                    f"Could not map subtitle language '{subtitle_row['language']}'."
                )
            subtitle = self._get_source_map_target(
                "subtitle", subtitle_row["id"], Subtitle
            )
            defaults = {
                "resource": content.get_resource(),
                "owner": self._get_target_owner(request_obj),
                "language": language,
                "name": subtitle_row["title"] or language.language,
                "is_original": True,
                "words": subtitle_row["words"] or "",
            }
            vtt_content = build_subtitle_vtt(subtitle_row["content"])
            if subtitle:
                for field_name, value in defaults.items():
                    setattr(subtitle, field_name, value)
                subtitle.subtitles_file.save(
                    f"legacy-{subtitle_row['id']}.vtt",
                    ContentFile(vtt_content.encode("utf-8")),
                    save=False,
                )
                subtitle.save()
            else:
                subtitle = Subtitle(**defaults)
                subtitle.subtitles_file.save(
                    f"legacy-{subtitle_row['id']}.vtt",
                    ContentFile(vtt_content.encode("utf-8")),
                    save=True,
                )
                self._upsert_source_map(
                    request_obj,
                    "subtitle",
                    subtitle_row["id"],
                    subtitle,
                )
            imported.append(subtitle)
        return imported

    def _import_courses(self, request_obj, collection):
        for course_row in request_obj.raw_snapshot.get("courses", []):
            course, _ = Course.objects.get_or_create(
                dept=(course_row["department"] or "").upper(),
                catalog_number=str(course_row["catalog_number"]).zfill(3),
                section_number=str(course_row["section_number"]).zfill(3),
            )
            collection.courses.add(course)

    def _apply_permissions(self, request_obj, collection, imported_resources):
        owner = self._get_target_owner(request_obj)
        if collection:
            CollectionUserAccess.objects.get_or_create(
                user=owner,
                collection=collection,
                defaults={"collection_role": CollectionRole.INSTRUCTOR},
            )

            snapshot_collection = request_obj.raw_snapshot.get("collection") or {}
            legacy_owner = request_obj.user_resolutions.filter(
                fingerprint=build_user_fingerprint(snapshot_collection.get("owner", {}))
            ).first()
            if (
                legacy_owner
                and legacy_owner.matched_user
                and legacy_owner.matched_user != owner
            ):
                CollectionUserAccess.objects.get_or_create(
                    user=legacy_owner.matched_user,
                    collection=collection,
                    defaults={"collection_role": CollectionRole.INSTRUCTOR},
                )

            for access_row in request_obj.raw_snapshot.get("collection_access", []):
                resolution = request_obj.user_resolutions.filter(
                    fingerprint=build_user_fingerprint(
                        {
                            "legacy_user_id": access_row["legacy_user_id"] or "",
                            "legacy_username": access_row["username"] or "",
                            "legacy_byu_id": access_row["byu_person_id"] or "",
                            "legacy_email": access_row["email"] or "",
                        }
                    )
                ).first()
                collection_role = self._resolve_collection_role(
                    access_row["account_role"]
                )
                if collection_role is None:
                    logger.warning(
                        "Skipping legacy collection access for %s: unknown "
                        "account_role %r.",
                        access_row.get("username") or "unknown user",
                        access_row.get("account_role"),
                    )
                    continue
                if (
                    resolution
                    and resolution.matched_user
                    and resolution.resolution_status
                    != LegacyMigrationUserResolutionStatus.SKIP
                ):
                    CollectionUserAccess.objects.get_or_create(
                        user=resolution.matched_user,
                        collection=collection,
                        defaults={"collection_role": collection_role},
                    )

        for resource in imported_resources:
            ResourceAccess.objects.get_or_create(user=owner, resource=resource)

        for migration_resource in request_obj.migration_resources.all():
            target_resource = self._get_source_map_target(
                "resource",
                migration_resource.legacy_resource_id,
                Resource,
            )
            if not target_resource:
                continue
            access_rows = next(
                (
                    resource_row["resource_access"]
                    for resource_row in request_obj.raw_snapshot.get("resources", [])
                    if resource_row["legacy_resource_id"]
                    == migration_resource.legacy_resource_id
                ),
                [],
            )
            for access_row in access_rows:
                resolution = request_obj.user_resolutions.filter(
                    fingerprint=build_user_fingerprint(
                        {
                            "legacy_user_id": access_row["legacy_user_id"] or "",
                            "legacy_username": access_row["username"] or "",
                            "legacy_byu_id": access_row["byu_person_id"] or "",
                            "legacy_email": access_row["email"] or "",
                        }
                    )
                ).first()
                if (
                    resolution
                    and resolution.matched_user
                    and resolution.resolution_status
                    != LegacyMigrationUserResolutionStatus.SKIP
                ):
                    ResourceAccess.objects.get_or_create(
                        user=resolution.matched_user,
                        resource=target_resource,
                    )

    def import_request(self, request_obj, job):
        self.sync_request_issues(request_obj)
        if request_obj.has_blocking_issues():
            raise ValueError("The migration request still has blocking issues.")

        owner = self._get_target_owner(request_obj)

        self._log_job_phase(job, "users")

        self._log_job_phase(job, "courses")
        collection = self._ensure_collection(request_obj, owner)
        if collection:
            self._import_courses(request_obj, collection)

        self._log_job_phase(job, "resources")
        imported_resources = []
        for migration_resource in request_obj.migration_resources.filter(include=True):
            imported_resources.append(
                self._determine_target_resource(request_obj, migration_resource, owner)
            )

        self._log_job_phase(job, "files")
        for file_decision in request_obj.file_decisions.select_related(
            "migration_resource"
        ):
            target_resource = self._get_source_map_target(
                "resource",
                file_decision.migration_resource.legacy_resource_id,
                Resource,
            )
            if target_resource:
                self._ensure_resource_file(request_obj, target_resource, file_decision)

        self._log_job_phase(job, "contents")
        for content_row in request_obj.raw_snapshot.get("contents", []):
            if not self._content_is_included(request_obj, content_row):
                continue
            target_resource = self._get_source_map_target(
                "resource",
                content_row["resource_id"],
                Resource,
            )
            target_file = None
            if content_row["resource_id"] and not content_row["resource_id"].startswith(
                "synthetic:"
            ):
                decision = (
                    request_obj.file_decisions.filter(
                        migration_resource__legacy_resource_id=content_row[
                            "resource_id"
                        ],
                        legacy_version=content_row["file_version"],
                    )
                    .exclude(action=LegacyMigrationFileAction.SKIP)
                    .first()
                )
                if decision:
                    target_file = self._get_source_map_target(
                        "file", decision.legacy_file_id, ResourceFile
                    )
                    if (
                        not target_file
                        and decision.action == LegacyMigrationFileAction.REUSE_EXISTING
                    ):
                        target_file = decision.selected_existing_resource_file

            content = self._ensure_content(
                request_obj,
                collection,
                content_row,
                target_resource,
                target_file,
            )
            content.clips.clear()
            for clip_index, clip_row in enumerate(
                parse_legacy_clips(content_row["clips"])
            ):
                self._ensure_clip(request_obj, content, clip_row, clip_index)

        self._log_job_phase(job, "subtitles")
        for content_row in request_obj.raw_snapshot.get("contents", []):
            if not self._content_is_included(request_obj, content_row):
                continue
            content = self._get_source_map_target("content", content_row["id"], Content)
            if not content:
                continue
            self._import_subtitles(
                request_obj, content, content_row.get("subtitles", [])
            )

        self._log_job_phase(job, "annotations")
        for content_row in request_obj.raw_snapshot.get("contents", []):
            if not self._content_is_included(request_obj, content_row):
                continue
            content = self._get_source_map_target("content", content_row["id"], Content)
            if not content:
                continue
            self._import_annotations(
                request_obj,
                content,
                parse_legacy_annotations(content_row["annotations"]),
            )

        self._log_job_phase(job, "permissions")
        self._apply_permissions(request_obj, collection, imported_resources)

        self._log_job_phase(job, "finalize")
