from collections import defaultdict
import datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.db import connections
from django.db import transaction
from django.utils import timezone
import xxhash

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover - fallback only matters if dependency is missing
    from difflib import SequenceMatcher

    class _FuzzFallback:
        @staticmethod
        def WRatio(left, right):
            return int(SequenceMatcher(None, left, right).ratio() * 100)

    fuzz = _FuzzFallback()

from .legacy_migration import LegacyMigrationFileAction
from .legacy_migration import LegacyMigrationFileDecision
from .legacy_migration import LegacyMigrationIssue
from .legacy_migration import LegacyMigrationIssueSeverity
from .legacy_migration import LegacyMigrationJob
from .legacy_migration import LegacyMigrationJobStatus
from .legacy_migration import LegacyMigrationJobType
from .legacy_migration import LegacyMigrationKind
from .legacy_migration import LegacyMigrationResource
from .legacy_migration import LegacyMigrationStatus
from .legacy_migration import LegacyMigrationUserResolution
from .legacy_migration import LegacyMigrationUserResolutionStatus
from .legacy_migration import LegacySourceMap
from .model_utils import create_or_update_user
from .models import AnnotationSet
from .models import BlankAnnotation
from .models import BlurAnnotation
from .models import BlurAnnotationPosition
from .models import Clip
from .models import Collection
from .models import CollectionRole
from .models import CollectionUserAccess
from .models import CommentAnnotation
from .models import Content
from .models import Course
from .models import Language
from .models import MuteAnnotation
from .models import PauseAnnotation
from .models import Resource
from .models import ResourceAccess
from .models import ResourceFile
from .models import SkipAnnotation
from .models import Subtitle
from .models import Track
from .models import User
from .utils import VTTCue
from .utils import build_vtt_file_string_from_cues
from .utils import seconds2hms

logger = logging.getLogger(__name__)

LEGACY_UUID_RE = re.compile(
    r"(?P<kind>collections|resources)?/?(?P<identifier>[0-9a-fA-F-]{36})"
)
LEGACY_PUBLIC_URL_RE = re.compile(
    r"/(?P<kind>collections|resources)/(?P<identifier>[0-9a-fA-F-]{36})"
)
LEGACY_URL_ONLY_RESOURCE_ID = "00000000-0000-0000-0000-000000000000"


def normalize_name(name):
    normalized = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def parse_legacy_reference(reference, requested_kind=None):
    raw_reference = (reference or "").strip()
    if not raw_reference:
        raise ValueError("A legacy URL or UUID is required.")

    url_match = LEGACY_PUBLIC_URL_RE.search(raw_reference)
    if url_match:
        discovered_kind = (
            LegacyMigrationKind.COLLECTION
            if url_match.group("kind") == "collections"
            else LegacyMigrationKind.RESOURCE
        )
        identifier = url_match.group("identifier")
    else:
        uuid_match = LEGACY_UUID_RE.search(raw_reference)
        if not uuid_match:
            raise ValueError("Could not find a legacy collection/resource UUID.")
        discovered_kind = requested_kind
        identifier = uuid_match.group("identifier")

    if requested_kind and discovered_kind and requested_kind != discovered_kind:
        raise ValueError(
            f"The provided reference points to a {discovered_kind}, not a {requested_kind}."
        )
    return discovered_kind or requested_kind, identifier


def timestamp_to_datetime(raw_timestamp):
    if raw_timestamp in (None, ""):
        return None
    if isinstance(raw_timestamp, datetime.datetime):
        return (
            raw_timestamp
            if timezone.is_aware(raw_timestamp)
            else timezone.make_aware(raw_timestamp)
        )
    return datetime.datetime.fromtimestamp(float(raw_timestamp), tz=datetime.UTC)


def json_loads_loose(value, default=None):
    if value in (None, ""):
        return [] if default is None else default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return [] if default is None else default


def parse_legacy_annotations(raw_annotations):
    if raw_annotations in (None, ""):
        return []
    parsed = json_loads_loose(raw_annotations, default=None)
    if parsed is not None:
        return parsed if isinstance(parsed, list) else []

    annotations = []
    for part in [item.strip() for item in raw_annotations.split("; ") if item.strip()]:
        try:
            annotations.append(json.loads(part))
        except json.JSONDecodeError:
            logger.warning("Skipping invalid legacy annotation payload: %s", part)
    return annotations


def parse_legacy_clips(raw_clips):
    clips = json_loads_loose(raw_clips, default=[])
    return clips if isinstance(clips, list) else []


def build_subtitle_vtt(raw_content):
    cues = []
    for cue in json_loads_loose(raw_content, default=[]):
        cues.append(
            VTTCue(
                type="CUE",
                payload=cue.get("text", ""),
                start_time=seconds2hms(float(cue.get("start", 0) or 0)),
                end_time=seconds2hms(float(cue.get("end", 0) or 0)),
            )
        )
    return build_vtt_file_string_from_cues(cues)


def map_legacy_media_type(legacy_value):
    value = (legacy_value or "").strip().lower()
    if value in {"vid", "video"}:
        return Resource.MediaType.VIDEO
    if value in {"aud", "audio"}:
        return Resource.MediaType.AUDIO
    if value in {"txt", "text"}:
        return Resource.MediaType.TEXT
    if value in {"www", "web"}:
        return Resource.MediaType.WEB
    return Resource.MediaType.VIDEO


def make_json_safe(value):
    if isinstance(value, dict):
        return {key: make_json_safe(subvalue) for key, subvalue in value.items()}
    if isinstance(value, list):
        return [make_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [make_json_safe(item) for item in value]
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


def resolve_legacy_file_path(legacy_path):
    raw_path = Path(legacy_path)
    if raw_path.is_absolute():
        return raw_path
    media_root = getattr(settings, "LEGACY_MIGRATION_MEDIA_ROOT", "")
    if not media_root:
        raise ImproperlyConfigured(
            "LEGACY_MIGRATION_MEDIA_ROOT must be configured for legacy file access."
        )
    return Path(media_root) / raw_path


def file_fingerprint_from_stat(path_obj, stat_result):
    return (
        int(stat_result.st_dev),
        int(stat_result.st_ino),
        int(stat_result.st_size),
        int(
            getattr(
                stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000)
            )
        ),
        str(path_obj.resolve()),
    )


def compute_checksum(path_obj):
    file_hash = xxhash.xxh64()
    with open(path_obj, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            file_hash.update(chunk)
    return file_hash.hexdigest()


def build_user_fingerprint(user_dict):
    parts = [
        user_dict.get("legacy_user_id", ""),
        user_dict.get("legacy_username", ""),
        user_dict.get("legacy_byu_id", ""),
        user_dict.get("legacy_email", ""),
    ]
    return "|".join(parts)


class LegacyCatalogClient:
    def __init__(self, alias=None):
        self.alias = alias or getattr(settings, "LEGACY_MIGRATION_DB_ALIAS", "legacy")
        if self.alias not in connections.databases:
            raise ImproperlyConfigured(
                f"Legacy database alias '{self.alias}' is not configured."
            )
        database_settings = connections.databases[self.alias]
        if database_settings.get("ENGINE") != "django.db.backends.sqlite3":
            raise ImproperlyConfigured("Legacy migration only supports SQLite dumps.")

        if self.alias == getattr(settings, "LEGACY_MIGRATION_DB_ALIAS", "legacy"):
            database_name_value = database_settings.get("NAME")
            if not database_name_value:
                raise ImproperlyConfigured(
                    "The legacy SQLite dump path is not configured."
                )
            database_name = Path(database_name_value)
            if not database_name.exists():
                raise ImproperlyConfigured(
                    "The legacy SQLite dump does not exist yet. Run "
                    "scripts/dump_legacy_to_sqlite.py before preflight."
                )

    def _fetchall(self, query, params=None):
        with connections[self.alias].cursor() as cursor:
            cursor.execute(query, params or [])
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]

    def _fetchone(self, query, params=None):
        rows = self._fetchall(query, params=params)
        return rows[0] if rows else None

    def get_collection(self, collection_id):
        return self._fetchone(
            """
            SELECT
                c.id,
                c.collection_name,
                c.owner,
                c.published,
                c.archived,
                c.public,
                c.copyrighted,
                u.username AS owner_username,
                u.byu_person_id AS owner_byu_id,
                u.email AS owner_email
            FROM collections c
            LEFT JOIN users u ON u.id = c.owner
            WHERE c.deleted IS NULL AND c.id = %s
            """,
            [collection_id],
        )

    def get_resource(self, resource_id):
        return self._fetchone(
            """
            SELECT
                r.id,
                r.resource_name,
                r.resource_type,
                r.requester_email,
                r.copyrighted,
                r.physical_copy_exists,
                r.full_video,
                r.published,
                r.views,
                r.metadata
            FROM resources r
            WHERE r.deleted IS NULL AND r.id = %s
            """,
            [resource_id],
        )

    def get_collection_contents(self, collection_id):
        return self._fetchall(
            """
            SELECT
                c.id,
                c.collection_id,
                c.resource_id,
                c.title,
                c.content_type,
                c.url,
                c.description,
                c.tags,
                c.annotations,
                c.thumbnail,
                c.allow_definitions,
                c.allow_notes,
                c.allow_captions,
                c.views,
                c.file_version,
                c.published,
                c.words,
                c.clips
            FROM contents c
            WHERE c.deleted IS NULL AND c.collection_id = %s
            ORDER BY c.created
            """,
            [collection_id],
        )

    def get_files_for_resources(self, resource_ids):
        if not resource_ids:
            return []
        placeholders = ", ".join(["%s"] * len(resource_ids))
        return self._fetchall(
            f"""
            SELECT
                f.id,
                f.resource_id,
                f.filepath,
                f.file_version,
                f.metadata,
                f.created,
                f.updated
            FROM files f
            WHERE f.deleted IS NULL AND f.resource_id IN ({placeholders})
            ORDER BY f.resource_id, f.file_version, f.created
            """,
            resource_ids,
        )

    def get_resource_access(self, resource_ids):
        if not resource_ids:
            return []
        placeholders = ", ".join(["%s"] * len(resource_ids))
        return self._fetchall(
            f"""
            SELECT
                ra.resource_id,
                ra.username,
                u.id AS legacy_user_id,
                u.byu_person_id,
                u.email
            FROM resource_access ra
            LEFT JOIN users u ON u.username = ra.username
            WHERE ra.deleted IS NULL AND ra.resource_id IN ({placeholders})
            ORDER BY ra.resource_id, ra.username
            """,
            resource_ids,
        )

    def get_collection_access(self, collection_id):
        return self._fetchall(
            """
            SELECT
                uca.collection_id,
                uca.account_role,
                u.username,
                u.id AS legacy_user_id,
                u.byu_person_id,
                u.email
            FROM user_collections_assoc uca
            JOIN users u ON u.username = uca.username
            WHERE uca.deleted IS NULL AND uca.collection_id = %s
            ORDER BY uca.account_role, u.username
            """,
            [collection_id],
        )

    def get_collection_courses(self, collection_id):
        return self._fetchall(
            """
            SELECT
                c.id,
                c.department,
                c.catalog_number,
                c.section_number
            FROM collection_courses_assoc cca
            JOIN courses c ON c.id = cca.course_id
            WHERE cca.deleted IS NULL
              AND c.deleted IS NULL
              AND cca.collection_id = %s
            ORDER BY c.department, c.catalog_number, c.section_number
            """,
            [collection_id],
        )

    def get_subtitles_for_contents(self, content_ids):
        if not content_ids:
            return []
        placeholders = ", ".join(["%s"] * len(content_ids))
        return self._fetchall(
            f"""
            SELECT
                s.id,
                s.title,
                s.language,
                s.content,
                s.words,
                s.content_id
            FROM subtitles s
            WHERE s.deleted IS NULL AND s.content_id IN ({placeholders})
            ORDER BY s.content_id, s.created
            """,
            content_ids,
        )

    def get_file_usage(self, resource_id, file_version):
        return self._fetchall(
            """
            SELECT
                c.id AS content_id,
                c.title AS content_title,
                col.id AS collection_id,
                col.collection_name,
                u.username AS collection_owner_username,
                uca.username AS access_username,
                uca.account_role
            FROM contents c
            LEFT JOIN collections col ON col.id = c.collection_id
            LEFT JOIN users u ON u.id = col.owner
            LEFT JOIN user_collections_assoc uca
                ON uca.collection_id = col.id AND uca.deleted IS NULL
            WHERE c.deleted IS NULL
              AND c.resource_id = %s
              AND c.file_version = %s
            ORDER BY col.collection_name, c.title
            """,
            [resource_id, file_version],
        )

    def build_collection_snapshot(self, collection_id):
        collection_row = self.get_collection(collection_id)
        if not collection_row:
            raise LookupError(f"Legacy collection {collection_id} was not found.")

        contents = self.get_collection_contents(collection_id)
        content_ids = [row["id"] for row in contents]
        resource_ids = {
            row["resource_id"]
            for row in contents
            if row["resource_id"] and row["resource_id"] != LEGACY_URL_ONLY_RESOURCE_ID
        }

        resources = []
        for resource_id in sorted(resource_ids):
            resource_row = self.get_resource(resource_id)
            if resource_row:
                resources.append(resource_row)

        resource_map = {resource["id"]: resource for resource in resources}
        files_by_resource = defaultdict(list)
        for file_row in self.get_files_for_resources(sorted(resource_ids)):
            files_by_resource[file_row["resource_id"]].append(file_row)

        resource_access_by_resource = defaultdict(list)
        for access_row in self.get_resource_access(sorted(resource_ids)):
            resource_access_by_resource[access_row["resource_id"]].append(access_row)

        subtitles_by_content = defaultdict(list)
        for subtitle_row in self.get_subtitles_for_contents(content_ids):
            subtitles_by_content[subtitle_row["content_id"]].append(subtitle_row)

        collection_access = self.get_collection_access(collection_id)
        courses = self.get_collection_courses(collection_id)

        snapshot_resources = []
        for resource_id in sorted(resource_ids):
            resource_row = resource_map[resource_id]
            snapshot_resources.append(
                {
                    "legacy_resource_id": resource_row["id"],
                    "name": resource_row["resource_name"],
                    "resource_type": resource_row["resource_type"],
                    "requester_email": resource_row["requester_email"],
                    "copyrighted": bool(resource_row["copyrighted"]),
                    "physical_copy_exists": bool(resource_row["physical_copy_exists"]),
                    "full_video": bool(resource_row["full_video"]),
                    "published": bool(resource_row["published"]),
                    "views": resource_row["views"] or 0,
                    "metadata": resource_row["metadata"],
                    "files": files_by_resource[resource_id],
                    "resource_access": resource_access_by_resource[resource_id],
                }
            )

        for content_row in contents:
            if content_row["resource_id"] == LEGACY_URL_ONLY_RESOURCE_ID:
                synthetic_resource_id = f"synthetic:{content_row['id']}"
                snapshot_resources.append(
                    {
                        "legacy_resource_id": synthetic_resource_id,
                        "name": content_row["title"] or "Legacy URL Content",
                        "resource_type": "www",
                        "requester_email": collection_row["owner_email"] or "",
                        "copyrighted": bool(collection_row["copyrighted"]),
                        "physical_copy_exists": False,
                        "full_video": False,
                        "published": bool(content_row["published"]),
                        "views": content_row["views"] or 0,
                        "metadata": {"synthetic_for_content_id": content_row["id"]},
                        "files": [],
                        "resource_access": [],
                    }
                )
                content_row["resource_id"] = synthetic_resource_id

            content_row["subtitles"] = subtitles_by_content[content_row["id"]]

        return {
            "kind": LegacyMigrationKind.COLLECTION,
            "collection": {
                "legacy_collection_id": collection_row["id"],
                "name": collection_row["collection_name"],
                "published": bool(collection_row["published"]),
                "archived": bool(collection_row["archived"]),
                "public": bool(collection_row["public"]),
                "copyrighted": bool(collection_row["copyrighted"]),
                "owner": {
                    "legacy_user_id": collection_row["owner"],
                    "legacy_username": collection_row["owner_username"] or "",
                    "legacy_byu_id": collection_row["owner_byu_id"] or "",
                    "legacy_email": collection_row["owner_email"] or "",
                },
            },
            "collection_access": collection_access,
            "courses": courses,
            "contents": contents,
            "resources": snapshot_resources,
        }

    def build_resource_snapshot(self, resource_id):
        resource_row = self.get_resource(resource_id)
        if not resource_row:
            raise LookupError(f"Legacy resource {resource_id} was not found.")

        files = self.get_files_for_resources([resource_id])
        resource_access = self.get_resource_access([resource_id])
        return {
            "kind": LegacyMigrationKind.RESOURCE,
            "resource": {
                "legacy_resource_id": resource_row["id"],
                "name": resource_row["resource_name"],
                "resource_type": resource_row["resource_type"],
                "requester_email": resource_row["requester_email"],
                "copyrighted": bool(resource_row["copyrighted"]),
                "physical_copy_exists": bool(resource_row["physical_copy_exists"]),
                "full_video": bool(resource_row["full_video"]),
                "published": bool(resource_row["published"]),
                "views": resource_row["views"] or 0,
                "metadata": resource_row["metadata"],
            },
            "resources": [
                {
                    "legacy_resource_id": resource_row["id"],
                    "name": resource_row["resource_name"],
                    "resource_type": resource_row["resource_type"],
                    "requester_email": resource_row["requester_email"],
                    "copyrighted": bool(resource_row["copyrighted"]),
                    "physical_copy_exists": bool(resource_row["physical_copy_exists"]),
                    "full_video": bool(resource_row["full_video"]),
                    "published": bool(resource_row["published"]),
                    "views": resource_row["views"] or 0,
                    "metadata": resource_row["metadata"],
                    "files": files,
                    "resource_access": resource_access,
                }
            ],
            "collection_access": [],
            "courses": [],
            "contents": [],
        }


class CurrentFileIndex:
    def __init__(self, checksum_cache):
        self.checksum_cache = checksum_cache
        self.by_realpath = defaultdict(list)
        self.by_inode = defaultdict(list)
        self.by_size = defaultdict(list)
        self.by_pk = {}
        self._load()

    def _build_usage_maps(self, resource_file_ids):
        collections_by_file = defaultdict(list)
        instructors_by_file = defaultdict(list)
        contents = (
            Content.objects.filter(resource_file_id__in=resource_file_ids)
            .select_related("collection__owner")
            .prefetch_related("collection__collectionuseraccess_set__user")
        )
        for content in contents:
            resource_file_id = content.resource_file_id
            if (
                content.collection
                and content.collection.name not in collections_by_file[resource_file_id]
            ):
                collections_by_file[resource_file_id].append(content.collection.name)
            if not content.collection:
                continue
            owner_label = content.collection.owner.netid
            if owner_label not in instructors_by_file[resource_file_id]:
                instructors_by_file[resource_file_id].append(owner_label)
            for access in content.collection.collectionuseraccess_set.all():
                if access.collection_role in {
                    CollectionRole.INSTRUCTOR,
                    CollectionRole.TA,
                }:
                    if access.user.netid not in instructors_by_file[resource_file_id]:
                        instructors_by_file[resource_file_id].append(access.user.netid)
        return collections_by_file, instructors_by_file

    def _load(self):
        resource_files = list(ResourceFile.objects.select_related("resource"))
        collections_by_file, instructors_by_file = self._build_usage_maps(
            [resource_file.pk for resource_file in resource_files]
        )
        for resource_file in resource_files:
            if not resource_file.file:
                continue
            try:
                path = Path(resource_file.file.path)
                stat_result = path.stat()
            except OSError:
                continue

            entry = {
                "resource_file_id": resource_file.pk,
                "resource_id": resource_file.resource_id,
                "resource_name": resource_file.resource.name,
                "version": resource_file.version,
                "path": resource_file.file.name,
                "absolute_path": str(path),
                "realpath": str(path.resolve()),
                "device": int(stat_result.st_dev),
                "inode": int(stat_result.st_ino),
                "size_bytes": int(stat_result.st_size),
                "checksum": resource_file.checksum or "",
                "collections": collections_by_file[resource_file.pk],
                "instructors": instructors_by_file[resource_file.pk],
            }
            self.by_realpath[entry["realpath"]].append(entry)
            self.by_inode[(entry["device"], entry["inode"])].append(entry)
            self.by_size[entry["size_bytes"]].append(entry)
            self.by_pk[entry["resource_file_id"]] = entry

    def get_entry(self, resource_file_id):
        return self.by_pk.get(resource_file_id)

    def _checksum_for_entry(self, entry):
        if entry["checksum"]:
            return entry["checksum"]
        checksum = self.checksum_cache.get_or_compute_path_checksum(
            Path(entry["absolute_path"])
        )
        entry["checksum"] = checksum
        return checksum

    def find_candidates(self, legacy_file_info):
        candidates = []
        seen_ids = set()

        def append_matches(entries, reason):
            for entry in entries:
                if entry["resource_file_id"] in seen_ids:
                    continue
                seen_ids.add(entry["resource_file_id"])
                candidates.append(
                    {
                        **entry,
                        "match_reason": reason,
                    }
                )

        realpath = legacy_file_info.get("realpath")
        if realpath:
            append_matches(self.by_realpath.get(realpath, []), "same_realpath")

        inode_key = (legacy_file_info.get("device"), legacy_file_info.get("inode"))
        if all(value is not None for value in inode_key):
            append_matches(self.by_inode.get(inode_key, []), "same_device_inode")

        if not candidates and legacy_file_info.get("size_bytes") is not None:
            source_checksum = self.checksum_cache.get_or_compute_legacy_checksum(
                legacy_file_info
            )
            if source_checksum:
                for entry in self.by_size.get(legacy_file_info["size_bytes"], []):
                    if self._checksum_for_entry(entry) == source_checksum:
                        append_matches([entry], "same_checksum")

        return candidates


class ChecksumCache:
    def __init__(self):
        self.cache = {}

    def _key_from_path(self, path_obj):
        stat_result = path_obj.stat()
        return file_fingerprint_from_stat(path_obj, stat_result)

    def get_or_compute_path_checksum(self, path_obj):
        key = self._key_from_path(path_obj)
        if key not in self.cache:
            self.cache[key] = compute_checksum(path_obj)
        return self.cache[key]

    def get_or_compute_legacy_checksum(self, legacy_file_info):
        try:
            path_obj = Path(legacy_file_info["absolute_path"])
            key = (
                legacy_file_info.get("device"),
                legacy_file_info.get("inode"),
                legacy_file_info.get("size_bytes"),
                legacy_file_info.get("mtime_ns"),
            )
            if key not in self.cache:
                self.cache[key] = compute_checksum(path_obj)
            return self.cache[key]
        except OSError:
            return ""


class LegacyMigrationService:
    def __init__(self, catalog_client=None, require_catalog=True):
        self.catalog_client = catalog_client
        if self.catalog_client is None and require_catalog:
            self.catalog_client = LegacyCatalogClient()
        self.checksum_cache = ChecksumCache()
        self.current_file_index = CurrentFileIndex(self.checksum_cache)

    def _get_catalog_client(self):
        if self.catalog_client is None:
            self.catalog_client = LegacyCatalogClient()
        return self.catalog_client

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

    def _resolve_user(self, legacy_user_dict):
        byu_id = legacy_user_dict.get("legacy_byu_id", "").strip()
        username = legacy_user_dict.get("legacy_username", "").strip()
        email = legacy_user_dict.get("legacy_email", "").strip().lower()

        if byu_id:
            user = User.objects.filter(byu_id=byu_id).first()
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
                created_user = create_or_update_user(byu_id)
                if created_user:
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

    def _get_legacy_file_info(self, file_row):
        absolute_path = resolve_legacy_file_path(file_row["filepath"])
        file_info = {
            "absolute_path": str(absolute_path),
            "realpath": "",
            "size_bytes": None,
            "device": None,
            "inode": None,
            "mtime_ns": None,
            "mtime_at": None,
            "atime_at": None,
            "extension": absolute_path.suffix.lower(),
        }
        try:
            stat_result = absolute_path.stat()
            file_info.update(
                {
                    "realpath": str(absolute_path.resolve()),
                    "size_bytes": int(stat_result.st_size),
                    "device": int(stat_result.st_dev),
                    "inode": int(stat_result.st_ino),
                    "mtime_ns": int(
                        getattr(
                            stat_result,
                            "st_mtime_ns",
                            int(stat_result.st_mtime * 1_000_000_000),
                        )
                    ),
                    "mtime_at": datetime.datetime.fromtimestamp(
                        stat_result.st_mtime,
                        tz=datetime.UTC,
                    ),
                    "atime_at": datetime.datetime.fromtimestamp(
                        stat_result.st_atime,
                        tz=datetime.UTC,
                    ),
                }
            )
        except OSError:
            logger.warning("Legacy file is missing on disk: %s", absolute_path)
        return file_info

    def _linked_usage_for_file(self, resource_id, file_version):
        usage_rows = self._get_catalog_client().get_file_usage(
            resource_id, file_version
        )
        linked_contents = []
        linked_collections = []
        linked_instructors = []
        for row in usage_rows:
            if row["content_id"]:
                linked_contents.append(
                    {
                        "legacy_content_id": row["content_id"],
                        "title": row["content_title"],
                    }
                )
            if row["collection_id"] and row["collection_name"]:
                collection_payload = {
                    "legacy_collection_id": row["collection_id"],
                    "name": row["collection_name"],
                }
                if collection_payload not in linked_collections:
                    linked_collections.append(collection_payload)
            if row["collection_owner_username"]:
                owner_label = row["collection_owner_username"]
                if owner_label not in linked_instructors:
                    linked_instructors.append(owner_label)
            if row["access_username"] and row["account_role"] in {0, 1}:
                if row["access_username"] not in linked_instructors:
                    linked_instructors.append(row["access_username"])
        return linked_contents, linked_collections, linked_instructors

    def preflight_request(self, request_obj):
        with transaction.atomic():
            migration_kind, legacy_identifier = parse_legacy_reference(
                request_obj.legacy_reference,
                requested_kind=request_obj.migration_kind,
            )
            request_obj.migration_kind = migration_kind
            request_obj.legacy_identifier = legacy_identifier

            if migration_kind == LegacyMigrationKind.COLLECTION:
                snapshot = self._get_catalog_client().build_collection_snapshot(
                    legacy_identifier
                )
            else:
                snapshot = self._get_catalog_client().build_resource_snapshot(
                    legacy_identifier
                )

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
                if not request_obj.target_owner:
                    request_obj.target_owner = request_obj.requested_by
                    request_obj.target_collection_name = (
                        request_obj.target_collection_name
                        or snapshot["collection"]["name"]
                    )
                    request_obj.save(
                        update_fields=["target_owner", "target_collection_name"]
                    )

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
                    provenance={
                        "requester_email": resource_payload.get("requester_email", ""),
                        "metadata": resource_payload.get("metadata", ""),
                        "copyrighted": bool(resource_payload.get("copyrighted", True)),
                        "physical_copy_exists": bool(
                            resource_payload.get("physical_copy_exists", False)
                        ),
                        "views": int(resource_payload.get("views") or 0),
                    },
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

                for file_row in resource_payload.get("files", []):
                    file_info = self._get_legacy_file_info(file_row)
                    linked_contents, linked_collections, linked_instructors = (
                        self._linked_usage_for_file(
                            resource_payload["legacy_resource_id"],
                            file_row["file_version"],
                        )
                    )
                    candidate_matches = self.current_file_index.find_candidates(
                        file_info
                    )
                    LegacyMigrationFileDecision.objects.create(
                        request=request_obj,
                        migration_resource=resource_row,
                        legacy_file_id=file_row["id"],
                        legacy_version=file_row["file_version"] or "",
                        target_version=file_row["file_version"] or "",
                        legacy_path=file_row["filepath"],
                        legacy_extension=file_info["extension"],
                        size_bytes=file_info["size_bytes"],
                        device=file_info["device"],
                        inode=file_info["inode"],
                        mtime_at=file_info["mtime_at"],
                        atime_at=file_info["atime_at"],
                        metadata={
                            "legacy_metadata": file_row["metadata"] or "",
                            "absolute_path": file_info["absolute_path"],
                            "realpath": file_info["realpath"],
                            "mtime_ns": file_info["mtime_ns"],
                        },
                        linked_contents=linked_contents,
                        linked_collections=linked_collections,
                        linked_instructors=linked_instructors,
                        candidate_matches=candidate_matches,
                    )

            self.sync_request_issues(request_obj)
            request_obj.status = LegacyMigrationStatus.NEEDS_REVIEW
            request_obj.save(update_fields=["status", "updated_at"])
            return request_obj

    def sync_request_issues(self, request_obj):
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
            and Collection.objects.filter(
                owner=request_obj.target_owner,
                name=request_obj.target_collection_name,
            ).exists()
        ):
            LegacyMigrationIssue.objects.create(
                request=request_obj,
                severity=LegacyMigrationIssueSeverity.BLOCKING,
                code="collection_name_conflict",
                message="The target owner already has a collection with the selected name.",
                details={"target_collection_name": request_obj.target_collection_name},
            )

        for migration_resource in request_obj.migration_resources.all():
            if not migration_resource.include:
                continue
            fuzzy_matches = migration_resource.fuzzy_matches or []
            if fuzzy_matches:
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

            reuse_resource_ids = {
                file_decision.selected_existing_resource_file.resource_id
                for file_decision in migration_resource.file_decisions.filter(
                    action=LegacyMigrationFileAction.REUSE_EXISTING,
                    selected_existing_resource_file__isnull=False,
                ).select_related("selected_existing_resource_file__resource")
            }
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

        for file_decision in request_obj.file_decisions.select_related(
            "migration_resource",
            "selected_existing_resource_file__resource",
        ):
            if not file_decision.migration_resource.include:
                continue
            if file_decision.size_bytes is None:
                LegacyMigrationIssue.objects.create(
                    request=request_obj,
                    migration_resource=file_decision.migration_resource,
                    file_decision=file_decision,
                    severity=LegacyMigrationIssueSeverity.BLOCKING,
                    code="missing_legacy_file",
                    message="The legacy file could not be found on disk during preflight.",
                    details={"path": file_decision.legacy_path},
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
        request_obj.status = LegacyMigrationStatus.APPROVED
        request_obj.save(update_fields=["status", "updated_at"])
        job = request_obj.queue_job(LegacyMigrationJobType.IMPORT)
        request_obj.status = LegacyMigrationStatus.QUEUED
        request_obj.save(update_fields=["status", "updated_at"])
        return job

    def queue_preflight(self, request_obj):
        request_obj.status = LegacyMigrationStatus.SUBMITTED
        request_obj.save(update_fields=["status", "updated_at"])
        return request_obj.queue_job(LegacyMigrationJobType.PREFLIGHT)

    def run_next_job(self):
        job = (
            LegacyMigrationJob.objects.filter(status=LegacyMigrationJobStatus.QUEUED)
            .order_by("created_at")
            .first()
        )
        if not job:
            return None
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
        except Exception as exc:
            logger.exception("Legacy migration job failed.")
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

    def _determine_target_resource(self, request_obj, migration_resource, owner):
        existing = self._get_source_map_target(
            "resource",
            migration_resource.legacy_resource_id,
            Resource,
        )
        if existing:
            return existing

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

        provenance = dict(migration_resource.provenance)
        notes = json.dumps(
            {
                "legacy_resource_id": migration_resource.legacy_resource_id,
                "legacy": provenance,
            },
            sort_keys=True,
        )
        target_resource = Resource.objects.create(
            name=migration_resource.target_resource_name
            or migration_resource.legacy_name,
            media_type=map_legacy_media_type(migration_resource.legacy_media_type),
            requester_netid=owner.netid,
            copyrighted=bool(provenance.get("copyrighted", True)),
            physical_copy_exists=bool(provenance.get("physical_copy_exists", False)),
            views=int(provenance.get("views", 0) or 0),
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
        source_path = Path(source_path)
        extension = source_path.suffix
        relative_name = f"{resource.name}/{version}{extension}"
        destination = Path(settings.MEDIA_ROOT) / relative_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
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
            file_decision.legacy_path
            if Path(file_decision.legacy_path).is_absolute()
            else str(resolve_legacy_file_path(file_decision.legacy_path)),
            target_resource,
            file_decision.target_version or file_decision.legacy_version,
        )
        resource_file = ResourceFile(
            resource=target_resource,
            version=file_decision.target_version or file_decision.legacy_version,
            full_video=True,
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
        for legacy_event in legacy_annotations:
            layer_number = int(legacy_event.get("layer", 0) or 0)
            if layer_number not in tracks_by_layer:
                tracks_by_layer[layer_number] = Track.objects.create(
                    annotation_set=annotation_set,
                    name=f"Imported Layer {layer_number}",
                    stack_position=layer_number,
                )

        for index, legacy_event in enumerate(legacy_annotations):
            model_class = self._annotation_model_for_type(legacy_event.get("type"))
            if not model_class:
                continue
            layer_number = int(legacy_event.get("layer", 0) or 0)
            track = tracks_by_layer[layer_number]
            start_time = float(legacy_event.get("start", 0) or 0)
            end_time = float(legacy_event.get("end", start_time) or start_time)
            common_kwargs = {
                "owner": self._get_target_owner(request_obj),
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
                annotation = model_class.objects.create(
                    message=legacy_event.get("message", ""),
                    end_time=start_time,
                    **common_kwargs,
                )
            elif model_class is CommentAnnotation:
                position = legacy_event.get("position") or {}
                annotation = model_class.objects.create(
                    text=legacy_event.get("comment") or legacy_event.get("text") or "",
                    x=float(position.get("x", 0) or 0),
                    y=float(position.get("y", 0) or 0),
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
                if (
                    resolution
                    and resolution.matched_user
                    and resolution.resolution_status
                    != LegacyMigrationUserResolutionStatus.SKIP
                ):
                    CollectionUserAccess.objects.get_or_create(
                        user=resolution.matched_user,
                        collection=collection,
                        defaults={"collection_role": int(access_row["account_role"])},
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
