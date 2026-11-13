"""What production sends a booth: one manifest holding the rows and the list of files.

The booth never receives annotation JSON. It receives the rows behind it, with their
original primary keys, and renders player JSON with the same code production uses.
Fingerprints (sha256 of the player JSON) let it prove the result is identical before
anything becomes playable.
"""

import hashlib
import json
import os
import time

from django.core import serializers
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
from ic.models import ICProgram
from ic.models import ICProgramItem

ANNOTATION_CLASSES = [
    SkipAnnotation,
    MuteAnnotation,
    BlankAnnotation,
    PauseAnnotation,
    BlurAnnotation,
    CommentAnnotation,
    Clip,
]

# Every model whose rows the booth mirrors, parents before children.
MIRRORED_MODELS = [
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


def _serialize(model, objects):
    if model is User:
        fields = USER_STUB_FIELDS
    else:
        # Everything but many-to-many, which the booth has no rows for.
        fields = [
            f.name
            for f in model._meta.concrete_fields
            if not f.primary_key and not f.many_to_many
        ]
    return json.loads(serializers.serialize("json", objects, fields=fields))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_fingerprint(content):
    player_json = content.get_player_json(include_subtitles=True)
    return hashlib.sha256(_canonical(player_json).encode("utf-8")).hexdigest()


def manifest_hash(manifest):
    return hashlib.sha256(_canonical(manifest).encode("utf-8")).hexdigest()


def file_xxh64(
    path, chunk_size=8 * 1024 * 1024, max_bytes_per_second=None, on_bytes=None
):
    """xxh64 of a file. max_bytes_per_second caps the read rate (the booth hashes from
    the disk the projector is playing from); on_bytes is called after each chunk."""
    digest = xxhash.xxh64()
    started = time.monotonic()
    read = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
            if on_bytes:
                on_bytes(len(chunk))
            if max_bytes_per_second:
                read += len(chunk)
                ahead = read / max_bytes_per_second - (time.monotonic() - started)
                if ahead > 0:
                    time.sleep(ahead)
    return digest.hexdigest()


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
        programs = list(ICProgram.syncable(on).order_by("pk"))
        items = list(ICProgramItem.objects.filter(program__in=programs).order_by("pk"))
        contents = list(
            Content.objects.filter(pk__in={i.content_id for i in items}).order_by("pk")
        )
        playlists = Playlist.objects.filter(
            pk__in={c.playlist_id for c in contents if c.playlist_id}
        ).order_by("pk")
        resource_ids = {c.resource_id for c in contents if c.resource_id}
        self.masters = list(
            ResourceFile.objects.filter(
                resource_id__in=resource_ids, is_projection_file=True
            ).order_by("pk")
        )
        file_ids = {c.resource_file_id for c in contents if c.resource_file_id}
        file_ids |= {m.pk for m in self.masters}
        file_ids |= set(
            ResourceFile.objects.filter(
                pk__in=file_ids, derived_from__isnull=False
            ).values_list("derived_from_id", flat=True)
        )
        annotation_sets = list(
            AnnotationSet.objects.filter(
                pk__in={c.annotation_set_id for c in contents if c.annotation_set_id}
            ).order_by("pk")
        )
        tracks = list(
            Track.objects.filter(annotation_set__in=annotation_sets).order_by("pk")
        )
        annotations = {
            model: list(model.objects.filter(track__in=tracks).order_by("pk"))
            for model in ANNOTATION_CLASSES
        }
        self.subtitles = list(
            Subtitle.objects.filter(resource_id__in=resource_ids).order_by("pk")
        )
        owner_ids = {p.owner_id for p in playlists}
        owner_ids |= {s.owner_id for s in annotation_sets if s.owner_id}
        owner_ids |= {s.owner_id for s in self.subtitles if s.owner_id}
        self.programs, self.items, self.contents = programs, items, contents
        self.rows = {
            Language: Language.objects.order_by("pk"),
            User: [
                _user_stub(u)
                for u in User.objects.filter(pk__in=owner_ids).order_by("pk")
            ],
            Resource: Resource.objects.filter(pk__in=resource_ids).order_by("pk"),
            ResourceFile: ResourceFile.objects.filter(pk__in=file_ids).order_by("pk"),
            Playlist: playlists,
            Subtitle: self.subtitles,
            AnnotationSet: annotation_sets,
            Track: tracks,
            **annotations,
            BlurAnchorPoint: BlurAnchorPoint.objects.filter(
                blur_annotation__in=annotations[BlurAnnotation]
            ).order_by("pk"),
            Content: contents,
            ICProgram: programs,
            ICProgramItem: items,
        }

    def fixture(self):
        """Serialized rows, in load order."""
        return [
            row
            for model in MIRRORED_MODELS
            for row in _serialize(model, self.rows[model])
        ]

    def _resource_ids_by_program(self):
        contents = {c.pk: c for c in self.contents}
        by_program = {}
        for item in self.items:
            resource_id = contents[item.content_id].resource_id
            if resource_id:
                by_program.setdefault(item.program_id, set()).add(resource_id)
        return by_program

    def files(self):
        """Every file the booth needs, with the dates of the programs that use it."""
        programs = {p.pk: p for p in self.programs}
        first_start, last_end = {}, {}
        for program_id, resource_ids in self._resource_ids_by_program().items():
            program = programs[program_id]
            for rid in resource_ids:
                first_start[rid] = min(
                    first_start.get(rid, program.start_date), program.start_date
                )
                last_end[rid] = max(
                    last_end.get(rid, program.end_date), program.end_date
                )

        def dates(resource_id):
            start, end = first_start.get(resource_id), last_end.get(resource_id)
            return {
                "resource_id": resource_id,
                "first_program_start": start.isoformat() if start else None,
                "last_program_end": end.isoformat() if end else None,
            }

        entries = []
        for master in self.masters:
            available = (
                master.master_released_at is None and master.bytes_on_this_server
            )
            entries.append(
                {
                    "kind": "projection",
                    "resource_file_id": master.pk,
                    **dates(master.resource_id),
                    "name": master.file.name,
                    "size": master.size_bytes or (master.file.size if available else 0),
                    "xxh64": master.checksum,
                    "available_from_parent": bool(available),
                }
            )
        for subtitle in self.subtitles:
            path = subtitle.subtitles_file.path
            entries.append(
                {
                    "kind": "subtitle",
                    "subtitle_id": subtitle.pk,
                    **dates(subtitle.resource_id),
                    "name": subtitle.subtitles_file.name,
                    "size": os.path.getsize(path),
                    "xxh64": file_xxh64(path),
                    "available_from_parent": True,
                }
            )
        return entries

    def manifest(self):
        from ic.readiness import program_problems

        resource_ids = self._resource_ids_by_program()
        return {
            "migrations": migration_leaves(),
            "fixture": self.fixture(),
            "files": self.files(),
            "fingerprints": {str(c.pk): content_fingerprint(c) for c in self.contents},
            "programs": [
                {
                    "id": program.pk,
                    "name": program.name,
                    "resource_ids": sorted(resource_ids.get(program.pk, ())),
                    "production_problems": [
                        p.message for p in program_problems(program) if p.is_error
                    ],
                }
                for program in self.programs
            ],
        }


def build_bundle(on=None):
    """(manifest, manifest hash) for the current syncable programs."""
    manifest = Bundle(on).manifest()
    return manifest, manifest_hash(manifest)


MANIFEST_HASH_CACHE_KEY = "ic_manifest_hash"
MANIFEST_HASH_CACHE_SECONDS = 15


def cached_manifest_hash():
    """The manifest hash, recomputed at most every few seconds.

    For requests that repeat (each booth's check every CHECK_INTERVAL_SECONDS, and
    the IC dashboard); building the bundle means rendering every scheduled film's
    player JSON. A change shows up at most this many seconds late.
    """
    from django.core.cache import cache

    digest = cache.get(MANIFEST_HASH_CACHE_KEY)
    if digest is None:
        digest = build_bundle()[1]
        cache.set(MANIFEST_HASH_CACHE_KEY, digest, MANIFEST_HASH_CACHE_SECONDS)
    return digest
