"""What production sends a booth: a manifest, a fixture of rows, and a list of files.

The booth never receives annotation JSON. It receives the rows behind it, with their
original primary keys, and renders player JSON with the same code production uses.
Fingerprints (sha256 of the canonical player JSON) let it prove the result is
identical before anything becomes playable.
"""

import hashlib
import json
import os
import subprocess
import time

from django.conf import settings
from django.core import serializers
from django.utils import timezone
import xxhash

from core.models import AnnotationSet
from core.models import BlankAnnotation
from core.models import BlurAnchorPoint
from core.models import BlurAnnotation
from core.models import Clip
from core.models import CommentAnnotation
from core.models import Content
from core.models import Language
from core.models import MuteAnnotation
from core.models import PauseAnnotation
from core.models import Playlist
from core.models import Resource
from core.models import ResourceFile
from core.models import SkipAnnotation
from core.models import Subtitle
from core.models import Track
from core.models import User

FORMAT_VERSION = 1

ANNOTATION_CLASSES = [
    SkipAnnotation,
    MuteAnnotation,
    BlankAnnotation,
    PauseAnnotation,
    BlurAnnotation,
    CommentAnnotation,
    Clip,
]


def mirrored_model_classes():
    """Every model whose rows the booth mirrors, parents before children."""
    from ic.models import ICProgram
    from ic.models import ICProgramItem

    return [
        Language,
        User,
        Resource,
        ResourceFile,
        Playlist,
        Subtitle,
        AnnotationSet,
        Track,
        *ANNOTATION_CLASSES,
        BlurAnchorPoint,
        Content,
        ICProgram,
        ICProgramItem,
    ]


def _concrete_field_names(model):
    """Fields to serialize: everything but many-to-many, which the booth has no rows for."""
    return [
        field.name
        for field in model._meta.concrete_fields
        if not field.primary_key and not field.many_to_many
    ]


def _user_stub(user):
    """Just enough of a user for FK integrity and owner labels. No netid, email or username."""
    return User(
        pk=user.pk,
        username=f"ic-mirror-{user.pk}",
        first_name=user.first_name,
        last_name=user.last_name,
        password="!",
        is_active=False,
        is_staff=False,
        is_superuser=False,
    )


USER_STUB_FIELDS = (
    "username",
    "first_name",
    "last_name",
    "password",
    "is_active",
    "is_staff",
    "is_superuser",
)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_player_json(player_json):
    """Order-insensitive form of get_player_json() for fingerprinting.

    The lists come from querysets without an ORDER BY on some paths, so two databases
    holding identical rows could list them differently. Sorting by id here keeps the
    fingerprint about content: a real difference in any field still changes it.
    """

    def by_id(items):
        return sorted(
            items,
            key=lambda item: (str(item.get("class_type", "")), item.get("id") or 0),
        )

    normalized = dict(player_json)
    for key in ("annotations", "clips", "tracks", "subtitleTracks"):
        if key in normalized:
            normalized[key] = by_id(normalized[key])
    for annotation in normalized.get("annotations", []):
        for key in ("positions", "blur_anchor_points"):
            if isinstance(annotation.get(key), list):
                annotation[key] = sorted(
                    annotation[key],
                    key=lambda point: (point.get("time", 0), point.get("id") or 0),
                )
    return normalized


def content_fingerprint(content):
    player_json = content.get_player_json(include_subtitles=True)
    return hashlib.sha256(
        _canonical(_canonical_player_json(player_json)).encode("utf-8")
    ).hexdigest()


def manifest_hash(manifest):
    without_time = {
        key: value for key, value in manifest.items() if key != "generated_at"
    }
    return hashlib.sha256(_canonical(without_time).encode("utf-8")).hexdigest()


def file_xxh64(path, chunk_size=8 * 1024 * 1024, max_bytes_per_second=None):
    """xxh64 of a file. max_bytes_per_second caps the read rate (the booth hashes from
    the disk the projector is playing from)."""
    digest = xxhash.xxh64()
    started = time.monotonic()
    read = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
            if max_bytes_per_second:
                read += len(chunk)
                ahead = read / max_bytes_per_second - (time.monotonic() - started)
                if ahead > 0:
                    time.sleep(ahead)
    return digest.hexdigest()


def cached_file_xxh64(path):
    """file_xxh64(), remembered per path, size and mtime.

    Every manifest build lists subtitle checksums, and the booth checks for changes
    every few seconds; the files rarely change, so they are hashed once per version.
    """
    from django.core.cache import cache

    stat = os.stat(path)
    key = (
        "ic-xxh64:"
        + hashlib.sha256(
            f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()
        ).hexdigest()
    )
    digest = cache.get(key)
    if digest is None:
        digest = file_xxh64(path)
        cache.set(key, digest, None)
    return digest


def git_sha():
    """The code version, so a booth never applies rows its own schema cannot read."""
    configured = os.environ.get("YVIDEO_GIT_SHA") or getattr(
        settings, "YVIDEO_GIT_SHA", ""
    )
    if configured:
        return configured
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def migration_leaves():
    """The latest applied migration of each app, as recorded in this database."""
    from django.db import connection
    from django.db.migrations.recorder import MigrationRecorder

    applied = MigrationRecorder(connection).applied_migrations()
    leaves = {}
    for app_label, name in applied:
        if app_label in ("core", "ic") and name > leaves.get(app_label, ""):
            leaves[app_label] = name
    return leaves


class Bundle:
    """The rows and files for every syncable program, computed once per request."""

    def __init__(self, on=None):
        from ic.models import ICProgram
        from ic.models import ICProgramItem

        self.programs = list(ICProgram.syncable(on).order_by("pk"))
        self.items = list(
            ICProgramItem.objects.filter(program__in=self.programs).order_by("pk")
        )
        content_ids = {item.content_id for item in self.items}
        self.contents = list(Content.objects.filter(pk__in=content_ids).order_by("pk"))
        self.playlists = list(
            Playlist.objects.filter(
                pk__in={c.playlist_id for c in self.contents if c.playlist_id}
            ).order_by("pk")
        )
        resource_ids = {c.resource_id for c in self.contents if c.resource_id}
        self.resources = list(
            Resource.objects.filter(pk__in=resource_ids).order_by("pk")
        )
        self.masters = list(
            ResourceFile.objects.filter(
                resource_id__in=resource_ids, is_projection_file=True
            ).order_by("pk")
        )
        resource_file_ids = {
            c.resource_file_id for c in self.contents if c.resource_file_id
        }
        resource_file_ids |= {m.pk for m in self.masters}
        content_files = ResourceFile.objects.filter(pk__in=resource_file_ids)
        resource_file_ids |= {
            f.derived_from_id for f in content_files if f.derived_from_id
        }
        self.resource_files = list(
            ResourceFile.objects.filter(pk__in=resource_file_ids).order_by("pk")
        )
        self.annotation_sets = list(
            AnnotationSet.objects.filter(
                pk__in={
                    c.annotation_set_id for c in self.contents if c.annotation_set_id
                }
            ).order_by("pk")
        )
        self.tracks = list(
            Track.objects.filter(annotation_set__in=self.annotation_sets).order_by("pk")
        )
        self.annotations = {
            model: list(model.objects.filter(track__in=self.tracks).order_by("pk"))
            for model in ANNOTATION_CLASSES
        }
        self.blur_anchor_points = list(
            BlurAnchorPoint.objects.filter(
                blur_annotation__in=self.annotations[BlurAnnotation]
            ).order_by("pk")
        )
        self.subtitles = list(
            Subtitle.objects.filter(resource_id__in=resource_ids).order_by("pk")
        )
        owner_ids = {p.owner_id for p in self.playlists}
        owner_ids |= {s.owner_id for s in self.annotation_sets if s.owner_id}
        owner_ids |= {s.owner_id for s in self.subtitles if s.owner_id}
        self.users = [
            _user_stub(user)
            for user in User.objects.filter(pk__in=owner_ids).order_by("pk")
        ]
        self.languages = list(Language.objects.order_by("pk"))

    def _serialize(self, model, objects):
        if model is User:
            fields = USER_STUB_FIELDS
        else:
            fields = _concrete_field_names(model)
        return json.loads(serializers.serialize("json", objects, fields=fields))

    def fixture(self):
        """JSON list of serialized rows, in load order."""
        from ic.models import ICProgram
        from ic.models import ICProgramItem

        rows_by_model = {
            Language: self.languages,
            User: self.users,
            Resource: self.resources,
            ResourceFile: self.resource_files,
            Playlist: self.playlists,
            Subtitle: self.subtitles,
            AnnotationSet: self.annotation_sets,
            Track: self.tracks,
            **self.annotations,
            BlurAnchorPoint: self.blur_anchor_points,
            Content: self.contents,
            ICProgram: self.programs,
            ICProgramItem: self.items,
        }
        rows = []
        for model in mirrored_model_classes():
            rows.extend(self._serialize(model, rows_by_model[model]))
        return _canonical(rows).encode("utf-8")

    def _program_dates_by_resource(self):
        first_start, last_end = {}, {}
        programs = {p.pk: p for p in self.programs}
        contents = {c.pk: c for c in self.contents}
        for item in self.items:
            program = programs[item.program_id]
            resource_id = contents[item.content_id].resource_id
            if not resource_id:
                continue
            start = first_start.get(resource_id)
            first_start[resource_id] = (
                min(start, program.start_date) if start else program.start_date
            )
            end = last_end.get(resource_id)
            last_end[resource_id] = (
                max(end, program.end_date) if end else program.end_date
            )
        return first_start, last_end

    def program_resource_ids(self, program):
        contents = {c.pk: c for c in self.contents}
        return sorted(
            {
                contents[i.content_id].resource_id
                for i in self.items
                if i.program_id == program.pk and contents[i.content_id].resource_id
            }
        )

    def files(self):
        entries = []
        first_start, last_end = self._program_dates_by_resource()
        for master in self.masters:
            available = (
                master.master_released_at is None and master.bytes_on_this_server
            )
            entries.append(
                {
                    "kind": "projection",
                    "resource_file_id": master.pk,
                    "resource_id": master.resource_id,
                    "first_program_start": (
                        first_start[master.resource_id].isoformat()
                        if master.resource_id in first_start
                        else None
                    ),
                    "name": master.file.name,
                    "size": master.size_bytes or (master.file.size if available else 0),
                    "xxh64": master.checksum,
                    "available_from_parent": bool(available),
                    "last_program_end": (
                        last_end[master.resource_id].isoformat()
                        if master.resource_id in last_end
                        else None
                    ),
                }
            )
        for subtitle in self.subtitles:
            path = subtitle.subtitles_file.path
            entries.append(
                {
                    "kind": "subtitle",
                    "subtitle_id": subtitle.pk,
                    "resource_id": subtitle.resource_id,
                    "first_program_start": (
                        first_start[subtitle.resource_id].isoformat()
                        if subtitle.resource_id in first_start
                        else None
                    ),
                    "name": subtitle.subtitles_file.name,
                    "size": os.path.getsize(path),
                    "xxh64": cached_file_xxh64(path),
                    "available_from_parent": True,
                }
            )
        return entries

    def production_problems(self, program):
        """Errors that keep a program from being ready, whatever the booth does."""
        from ic.readiness import program_problems

        return [
            problem.message for problem in program_problems(program) if problem.is_error
        ]

    def fingerprints(self):
        return {
            str(content.pk): content_fingerprint(content) for content in self.contents
        }

    def manifest(self, fixture_bytes=None):
        fixture_bytes = fixture_bytes if fixture_bytes is not None else self.fixture()
        return {
            "format": FORMAT_VERSION,
            "generated_at": timezone.now().isoformat(),
            "git_sha": git_sha(),
            "migrations": migration_leaves(),
            "fixture": {
                "sha256": hashlib.sha256(fixture_bytes).hexdigest(),
                "size": len(fixture_bytes),
            },
            "files": self.files(),
            "fingerprints": self.fingerprints(),
            "programs": [
                {
                    "id": program.pk,
                    "name": program.name,
                    "start_date": program.start_date.isoformat(),
                    "end_date": program.end_date.isoformat(),
                    "item_ids": [
                        i.pk for i in self.items if i.program_id == program.pk
                    ],
                    "content_ids": [
                        i.content_id for i in self.items if i.program_id == program.pk
                    ],
                    "resource_ids": self.program_resource_ids(program),
                    "production_problems": self.production_problems(program),
                }
                for program in self.programs
            ],
        }


def build_bundle(on=None):
    """(manifest, fixture bytes, manifest hash) for the current syncable programs."""
    bundle = Bundle(on)
    fixture_bytes = bundle.fixture()
    manifest = bundle.manifest(fixture_bytes)
    return manifest, fixture_bytes, manifest_hash(manifest)


def current_manifest_hash(on=None):
    return build_bundle(on)[2]


MANIFEST_HASH_CACHE_KEY = "ic_manifest_hash"
MANIFEST_HASH_CACHE_SECONDS = 15


def cached_manifest_hash():
    """The manifest hash, recomputed at most every few seconds.

    For requests that repeat (each booth's check every IC_CHECK_INTERVAL_SECONDS, and
    the IC dashboard); building the bundle means rendering every scheduled film's
    player JSON. A change shows up at most this many seconds late.
    """
    from django.core.cache import cache

    digest = cache.get(MANIFEST_HASH_CACHE_KEY)
    if digest is None:
        digest = current_manifest_hash()
        cache.set(MANIFEST_HASH_CACHE_KEY, digest, MANIFEST_HASH_CACHE_SECONDS)
    return digest
