"""SQL-data-only bulk import from a PostgreSQL dump or legacy SQLite snapshot."""

from collections import Counter
from collections import defaultdict
from contextlib import closing
import datetime
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import connection
from django.db import transaction
from django.db.models import Model
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import AnnotationSet
from ..models import Content
from ..models import Course
from ..models import Language
from ..models import Playlist
from ..models import PlaylistUserAccess
from ..models import PrivilegeLevel
from ..models import Resource
from ..models import ResourceAccess
from ..models import ResourceFile
from ..models import Subtitle
from ..models import User
from . import LEGACY_USERNAME_PREFIX
from .annotations import annotation_model_for_type
from .annotations import import_annotations
from .legacy_roles import map_legacy_collection_role
from .models import LegacySourceMap
from .parsers import LEGACY_URL_ONLY_RESOURCE_ID
from .parsers import map_legacy_media_type
from .sql_dump import iter_postgres_copy_rows

# Keep the source scope explicit: do not import Django's old auth_user table.
REQUIRED_COLUMNS = {
    "users": {"username", "byu_person_id", "email", "account_name", "account_type"},
    "collections": {"collection_name", "owner", "published", "archived"},
    "user_collections_assoc": {"username", "collection_id", "account_role"},
    "courses": {"department", "catalog_number", "section_number"},
    "resources": {
        "resource_name",
        "resource_type",
        "requester_email",
        "copyrighted",
        "physical_copy_exists",
        "full_video",
        "views",
        "metadata",
    },
    "resource_access": {"username", "resource_id", "last_verified"},
    "files": {"resource_id", "filepath", "file_version", "metadata"},
    "contents": {
        "title",
        "collection_id",
        "resource_id",
        "file_id",
        "file_version",
        "url",
        "description",
        "allow_definitions",
        "allow_captions",
        "views",
        "published",
        "annotations",
        "clips",
    },
    "subtitles": {"title", "language", "content", "content_id"},
}


_LEGACY_DEPARTMENT_ALIASES = {
    "CS": "C S",
    "COMPSTDS": "CMPST",
    "ENGLSASDF": "ENGL",
    "ENG": "ENGL",
    "EURO": "EUROP",
    "GERMAN AND RUSSIAN": "GERM",
    "GERMAN&RUSSIAN": "GERM",
    "HONORS": "HONRS",
    "KOR": "KOREA",
    "KOREAN": "KOREA",
    "LINGT": "LING",
    "LINGUISTICS": "LING",
    "NORWEGIAN": "NORWE",
    "PHILOSOPHY": "PHIL",
    "PHYS": "PHY S",
    "PORTUGUESE": "PORT",
    "RELC": "REL C",
    "RUSSIAN": "RUSS",
    "SCAN": "SCAND",
    "SCANDINAVIAN": "SCAND",
    "SPANISH": "SPAN",
}


_LEGACY_TEXT_TIMESTAMP = re.compile(
    r"^(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+)?"
    r"(?P<month>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)\s+"
    r"(?P<zone>\S+)\s+(?P<year>\d{4})$",
    re.IGNORECASE,
)
_LEGACY_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ),
        start=1,
    )
}
# The legacy BYU app emitted North American abbreviations. Fixed offsets
# preserve MST versus MDT independently of this machine's timezone or DST.
_LEGACY_ZONE_MINUTES = {
    "UTC": 0,
    "GMT": 0,
    "UT": 0,
    "Z": 0,
    "EST": -300,
    "EDT": -240,
    "CST": -360,
    "CDT": -300,
    "MST": -420,
    "MDT": -360,
    "PST": -480,
    "PDT": -420,
    "AKST": -540,
    "AKDT": -480,
    "HST": -600,
    "AST": -240,
    "ADT": -180,
    "NST": -210,
    "NDT": -150,
}


class BulkImportError(ValueError):
    pass


class LegacyBulkImporter:
    def __init__(self, *, source, yearterm, user_map=None, commit=False):
        self.source = Path(source).expanduser().resolve()
        self.dept_map_path = self.source.with_name(self.source.name + ".dept_map")
        self.dept_map_written = False
        self.cleaned_map_path = self.source.with_name(self.source.name + ".cleaned-map")
        self.cleaned_map_written = False
        self.yearterm = yearterm
        self.user_map_path = user_map
        self.commit = commit
        self.rows = {}
        self.objects = defaultdict(dict)
        self.users_by_netid = defaultdict(list)
        self.source_files_by_version = defaultdict(list)
        self.counts = {
            name: {"created": 0, "reused": 0, "deleted": 0, "skipped": 0}
            for name in REQUIRED_COLUMNS
        }
        self.created_access = set()
        self.skipped_users = {}
        self._annotation_set_has_retired_column = None
        self.context = "configuration"

    def run(self):
        """Transfer database records only; roll back unless commit was requested."""
        try:
            if not re.fullmatch(r"\d{4}[1345]", self.yearterm or ""):
                raise BulkImportError(
                    "--yearterm must be YYYY plus 1, 3, 4, or 5 (e.g. 20265)."
                )
            if not self.source.is_file():
                raise BulkImportError(
                    f"Legacy source file does not exist: {self.source}"
                )

            target = connection.settings_dict.get("NAME")
            if (
                target
                and str(target) != ":memory:"
                and Path(target).resolve() == self.source
            ):
                raise BulkImportError(
                    "Source file and target database must be different files."
                )
            user_map = self._read_user_map()
            self._read_source()
            self._write_dept_map()
            self.context = "user mapping"
            unknown = set(user_map) - set(self.rows["users"])
            if unknown:
                raise BulkImportError(
                    f"--user-map contains unknown/deleted legacy user IDs: {sorted(unknown)}"
                )
            self.user_map = user_map
            self._filter_excluded_users()
            self._filter_excluded_owned_collections()
            self._expand_course_sections()
            with transaction.atomic(durable=True):
                self._transfer()
                self._write_cleaned_dept_map()
                if not self.commit:
                    transaction.set_rollback(True)
            return self.counts
        except Exception as exc:
            raise BulkImportError(f"{self.context}: {exc}") from exc

    def _read_user_map(self):
        if not self.user_map_path:
            return {}
        data = json.loads(Path(self.user_map_path).read_text())
        if not isinstance(data, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) or not value.strip()
            for key, value in data.items()
        ):
            raise BulkImportError(
                "--user-map must be a JSON object: legacy user UUID -> existing username."
            )
        return data

    def _read_source(self):
        self.context = "source data"
        with self.source.open("rb") as handle:
            signature = handle.read(16)
        if signature == b"SQLite format 3\x00":
            with closing(
                sqlite3.connect(self.source.as_uri() + "?mode=ro", uri=True)
            ) as source:
                source.row_factory = sqlite3.Row
                source.execute("PRAGMA query_only=ON")
                source.execute("BEGIN")
                self._read_snapshot(source)
        elif signature.startswith(b"PGDMP"):
            raise BulkImportError(
                "Custom-format PostgreSQL archives are not supported; supply a plain-text .sql dump."
            )
        else:
            self._read_sql_dump()
        for table in self.rows:
            self.rows[table] = dict(sorted(self.rows[table].items()))
        for row in self.rows["files"].values():
            self.source_files_by_version[
                (row["resource_id"], row["file_version"] or "")
            ].append(row)

    def _write_dept_map(self):
        self.context = f"department map ({self.dept_map_path})"
        # Count original names before normalization or multi-section expansion.
        counts = Counter(
            row["department"]
            for row in self.rows["courses"].values()
            if row["department"] is not None
        )
        self.dept_map_path.write_text(
            json.dumps(dict(counts), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        self.dept_map_written = True

    def _write_cleaned_dept_map(self):
        self.context = f"cleaned department map ({self.cleaned_map_path})"
        # Count each destination course once, including expanded sections but
        # not duplicate legacy rows that resolve to the same destination.
        courses = {course.pk: course for course in self.objects["courses"].values()}
        counts = Counter(course.dept for course in courses.values())
        self.cleaned_map_path.write_text(
            json.dumps(dict(counts), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        self.cleaned_map_written = True

    def _check_columns(self, table, columns):
        missing = (
            REQUIRED_COLUMNS[table] | {"id", "deleted", "created", "updated"}
        ) - set(columns)
        if missing:
            raise BulkImportError(
                f"{table} is missing columns: {', '.join(sorted(missing))}"
            )
        self.rows[table] = {}

    def _add_source_row(self, table, row):
        if row["deleted"] is not None:
            self.counts[table]["deleted"] += 1
            return
        identifier = row["id"]
        if not isinstance(identifier, str) or not identifier or len(identifier) > 100:
            raise BulkImportError(f"{table} has an invalid source ID: {identifier!r}")
        if identifier in self.rows[table]:
            raise BulkImportError(f"{table} has duplicate ID {identifier}")
        self.rows[table][identifier] = row

    def _read_sql_dump(self):
        self.context = "PostgreSQL SQL dump"
        for table, columns, row in iter_postgres_copy_rows(
            self.source, REQUIRED_COLUMNS
        ):
            if row is None:
                self._check_columns(table, columns)
            else:
                self._add_source_row(table, row)

    def _read_snapshot(self, source):
        self.context = "source snapshot"
        for table in REQUIRED_COLUMNS:
            columns = {
                row["name"] for row in source.execute(f'PRAGMA table_info("{table}")')
            }
            self._check_columns(table, columns)
            for raw in source.execute(f'SELECT * FROM "{table}" ORDER BY id'):
                self._add_source_row(table, dict(raw))

    @staticmethod
    def _valid_byu_id(raw):
        byu_id = (raw or "").strip()
        return bool(re.fullmatch(r"\d{9}", byu_id)) and byu_id != "000000000"

    def _filter_excluded_users(self):
        users = self.rows["users"]
        excluded = {
            identifier
            for identifier, row in users.items()
            if identifier not in self.user_map
            and (
                not self._valid_byu_id(row["byu_person_id"])
                or not (row["username"] or "").strip()
                or str(row["account_type"]).strip() == "3"
            )
        }
        self.skipped_users = {
            identifier: {
                "netid": users[identifier]["username"],
                "byu_person_id": users[identifier]["byu_person_id"],
                "account_type": users[identifier]["account_type"],
            }
            for identifier in excluded
        }

        # Associations identify users by netid, so do not exclude associations
        # when another retained legacy account has the same netid.
        retained_netids = {
            row["username"]
            for identifier, row in users.items()
            if identifier not in excluded
        }
        excluded_netids = {
            users[identifier]["username"] for identifier in excluded
        } - retained_netids
        for identifier in excluded:
            del users[identifier]
        self.counts["users"]["skipped"] = len(excluded)
        for table in ("user_collections_assoc", "resource_access"):
            identifiers = [
                identifier
                for identifier, row in self.rows[table].items()
                if row["username"] in excluded_netids
            ]
            for identifier in identifiers:
                del self.rows[table][identifier]
            self.counts[table]["skipped"] = len(identifiers)

    def _filter_excluded_owned_collections(self):
        excluded_collections = {
            identifier
            for identifier, row in self.rows["collections"].items()
            if row["owner"] in self.skipped_users
        }
        excluded_contents = {
            identifier
            for identifier, row in self.rows["contents"].items()
            if row["collection_id"] in excluded_collections
        }
        excluded = {
            "collections": excluded_collections,
            "contents": excluded_contents,
        }
        for table, field, parent_ids in (
            ("subtitles", "content_id", excluded_contents),
            ("user_collections_assoc", "collection_id", excluded_collections),
        ):
            excluded[table] = {
                identifier
                for identifier, row in self.rows[table].items()
                if row[field] in parent_ids
            }
        for table, identifiers in excluded.items():
            for identifier in identifiers:
                del self.rows[table][identifier]
            self.counts[table]["skipped"] += len(identifiers)

    def _expand_course_sections(self):
        courses = {}
        for identifier, row in self.rows["courses"].items():
            self.context = f"courses[{identifier}]"
            raw_sections = row["section_number"] or ""
            if not re.search(r"[,-]", raw_sections):
                courses[identifier] = row

                continue
            sections = []
            for raw_section in re.split(r"[,-]", raw_sections):
                if not raw_section.strip():
                    raise BulkImportError(
                        f"Empty section in section_number={raw_sections!r}."
                    )
                section = raw_section.strip().replace("O", "0").zfill(3)
                if section not in sections:
                    sections.append(section)

            for index, section in enumerate(sections):
                # Keep the original mapping for the first section; additional
                # sections get stable IDs so course reruns are safe.
                source_id = (
                    identifier if index == 0 else f"{identifier}:section:{section}"
                )
                courses[source_id] = {
                    **row,
                    "id": source_id,
                    "section_number": section,
                    "_source_row": row,
                }

        self.rows["courses"] = courses

    def mapped(self, source_type, source_id, model):
        mapping = LegacySourceMap.objects.filter(
            source_system="legacy", source_type=source_type, source_id=str(source_id)
        ).first()
        if mapping is None:
            return None
        if mapping.target_model != model.__name__:
            raise BulkImportError(
                f"Source map {source_type}/{source_id} targets {mapping.target_model}, expected {model.__name__}."
            )
        target = model.objects.filter(pk=mapping.target_id).first()
        if target is None:
            raise BulkImportError(
                f"Stale source map {source_type}/{source_id}: target {mapping.target_id} no longer exists."
            )
        if source_type == "course" and target.yearterm != self.yearterm:
            raise BulkImportError(
                f"Course {source_id} was already mapped to term {target.yearterm}; refusing to change its term."
            )
        return target

    def record(self, source_type, source_id, target, metadata):
        LegacySourceMap.objects.create(
            source_system="legacy",
            source_type=source_type,
            source_id=str(source_id),
            target_model=type(target).__name__,
            target_id=target.pk,
            metadata=metadata,
        )

    def _transfer(self):
        steps = [
            ("users", "user", User, self._user),
            ("courses", "course", Course, self._course),
            ("resources", "resource", Resource, self._resource),
            ("collections", "collection", Playlist, self._collection),
            ("files", "file", ResourceFile, self._file),
            ("contents", "content", Content, self._content),
            ("subtitles", "subtitle", Subtitle, self._subtitle),
            (
                "user_collections_assoc",
                "collection_access",
                PlaylistUserAccess,
                self._collection_access,
            ),
            (
                "resource_access",
                "resource_access",
                ResourceAccess,
                self._resource_access,
            ),
        ]
        for table, source_type, model, handler in steps:
            for identifier, row in self.rows[table].items():
                self.context = f"{table}[{identifier}]"
                if table in {"files", "resource_access"} and (
                    row["resource_id"] not in self.objects["resources"]
                ):
                    self.counts[table]["skipped"] += 1
                    continue
                if (
                    table == "contents"
                    and row["collection_id"] not in self.objects["collections"]
                ):
                    self.counts[table]["skipped"] += 1
                    continue
                if table == "subtitles" and (
                    row["content_id"] not in self.objects["contents"]
                    or not (row["language"] or "").strip()
                ):
                    self.counts[table]["skipped"] += 1
                    continue
                if table in {"user_collections_assoc", "resource_access"} and (
                    not self.users_by_netid.get(row["username"])
                ):
                    self.counts[table]["skipped"] += 1
                    continue
                target = self.mapped(source_type, identifier, model)
                created = False
                if target is None:
                    target, created = handler(row)
                    if target is None:
                        self.counts[table]["skipped"] += 1
                        continue
                    self.record(
                        source_type,
                        identifier,
                        target,
                        {
                            "bulk_import": True,
                            "table": table,
                            "source_row": row.get("_source_row", row),
                            "yearterm": self.yearterm if table == "courses" else None,
                            "media_pending": table in {"files", "subtitles"},
                        },
                    )
                    self.counts[table]["created" if created else "reused"] += 1
                    if created:
                        self._timestamps(target, row)
                else:
                    if table == "users" and identifier in self.user_map:
                        if target.username != self.user_map[identifier]:
                            raise BulkImportError(
                                "--user-map conflicts with the existing source mapping."
                            )
                    if table == "contents":
                        events = self._events(row["annotations"], annotations=True)
                        clips = self._events(row["clips"])
                        if (events or clips) and target.annotation_set_id is None:
                            raise BulkImportError(
                                "Mapped content has legacy annotations/clips but no annotation set. "
                                "Review the incomplete prior import before rerunning."
                            )
                    self.counts[table]["reused"] += 1

                self.objects[table][identifier] = target
                if table == "users" and row["username"]:
                    self.users_by_netid[row["username"]].append(target)

    def _require(self, table, identifier):
        target = self.objects[table].get(identifier)
        if target is None:
            if table == "users" and identifier in self.skipped_users:
                skipped_user = self.skipped_users[identifier]
                raise BulkImportError(
                    f"Required owner with netid {skipped_user['netid']!r} "
                    f"({identifier}) was excluded (BYU Person ID "
                    f"{skipped_user['byu_person_id']!r}, "
                    f"account_type={skipped_user['account_type']!r}); "
                    "cannot import this collection without an owner."
                )
            raise BulkImportError(
                f"Missing, deleted, or unimported {table} reference: {identifier!r}."
            )
        return target

    def _user_by_netid(self, netid):
        users = {user.pk: user for user in self.users_by_netid.get(netid, [])}
        if len(users) != 1:
            raise BulkImportError(
                f"Legacy username {netid!r} resolves to {len(users)} users; resolve users with --user-map."
            )
        return next(iter(users.values()))

    def _save(self, target):
        for field in target._meta.fields:
            value = getattr(target, field.attname)
            if (
                field.max_length
                and isinstance(value, str)
                and len(value) > field.max_length
            ):
                raise BulkImportError(
                    f"{type(target).__name__}.{field.name} exceeds {field.max_length} characters."
                )
        if isinstance(target, (Resource, ResourceFile)):
            # SQL-only import: bypass identifier generation and file/checksum I/O.
            if isinstance(target, Resource):
                target.imdb_id = None
            else:
                target.barcode = None
            Model.save(target)
        else:
            target.save()
        return target

    def _timestamps(self, target, row):
        values = {}
        for old, new in (("created", "created_at"), ("updated", "updated_at")):
            if hasattr(target, new) and row.get(old) not in (None, ""):
                values[new] = self._datetime(row[old])
        if isinstance(target, User):
            if row.get("created"):
                values["date_joined"] = self._datetime(row["created"])
            if row.get("last_login"):
                values["last_login"] = self._datetime(row["last_login"])
        if isinstance(target, ResourceAccess) and row.get("last_verified"):
            values["last_verified"] = self._datetime(row["last_verified"])
        if values:
            type(target).objects.filter(pk=target.pk).update(**values)

    @staticmethod
    def _datetime(raw):
        if raw is None or (isinstance(raw, str) and raw.strip().casefold() == "na"):
            return None
        try:
            if isinstance(raw, datetime.datetime):
                value = raw
            elif isinstance(raw, (int, float)) or re.fullmatch(
                r"[+-]?\d+(?:\.\d+)?", str(raw).strip()
            ):
                value = datetime.datetime.fromtimestamp(float(raw), tz=datetime.UTC)
            else:
                text = str(raw).strip()
                value = parse_datetime(text)
                if value is None:
                    value = LegacyBulkImporter._text_datetime(text)
        except (ValueError, OverflowError, OSError) as exc:
            raise BulkImportError(f"Invalid legacy timestamp {raw!r}: {exc}") from exc
        if value is None:
            raise BulkImportError(f"Invalid legacy timestamp: {raw!r}")
        # Only timestamps without an explicit timezone inherit Django TIME_ZONE.
        return timezone.make_aware(value) if timezone.is_naive(value) else value

    @staticmethod
    def _text_datetime(text):
        match = _LEGACY_TEXT_TIMESTAMP.fullmatch(text)
        if match is None:
            return None
        month = _LEGACY_MONTHS.get(match["month"].lower())
        if month is None:
            raise ValueError(f"Unknown month {match['month']!r}.")
        zone = match["zone"].upper()
        minutes = _LEGACY_ZONE_MINUTES.get(zone)
        if minutes is None:
            offset = re.fullmatch(r"(?:GMT|UTC)?([+-])(\d{2}):?(\d{2})", zone)
            if offset is None:
                raise ValueError(
                    f"Unsupported timezone {zone!r}; refusing to guess its offset."
                )
            hours, remainder = int(offset[2]), int(offset[3])
            if hours > 23 or remainder > 59:
                raise ValueError(f"Invalid timezone offset {zone!r}.")
            minutes = (hours * 60 + remainder) * (-1 if offset[1] == "-" else 1)
        value = datetime.datetime.fromisoformat(
            f"{match['year']}-{month:02d}-{int(match['day']):02d}T{match['time']}"
        )
        return value.replace(
            tzinfo=datetime.timezone(datetime.timedelta(minutes=minutes))
        )

    @staticmethod
    def _boolean(raw, default=False):
        if raw is None:
            return default
        if raw in (True, 1, "1", "t", "true"):
            return True
        if raw in (False, 0, "0", "f", "false"):
            return False
        raise BulkImportError(f"Invalid legacy boolean: {raw!r}")

    @staticmethod
    def _name(queryset, field, raw, identifier, limit):
        base = (raw or "Untitled").strip() or "Untitled"
        base = base[:limit]
        if not queryset.filter(**{field: base}).exists():
            return base
        suffix = f" [legacy {identifier}]"
        candidate = base[: limit - len(suffix)] + suffix
        if queryset.filter(**{field: candidate}).exists():
            raise BulkImportError(
                f"Name collision even after legacy-ID suffix: {candidate!r}"
            )
        return candidate

    def _user(self, row):
        if row["id"] in self.user_map:
            target = User.objects.filter(username=self.user_map[row["id"]]).first()
            if target is None:
                raise BulkImportError("--user-map must name an existing target user.")
            return target, False
        netid = (row["username"] or "").strip()
        if len(netid) > 8:
            raise BulkImportError(
                "Legacy netid exceeds eight characters; map this user explicitly with --user-map."
            )
        candidates = list(User.objects.filter(netid__iexact=netid).order_by("pk"))
        if len(candidates) > 1:
            identifiers = ", ".join(str(user.pk) for user in candidates)
            raise BulkImportError(
                f"Netid {netid!r} belongs to multiple target users ({identifiers}); "
                "supply --user-map to choose one."
            )
        if candidates:
            return candidates[0], False
        first, _, last = (row["account_name"] or "").strip().partition(" ")
        # Legacy stores the BYU Person ID, not the BYU ID that login matches on,
        # so login renames this account once the user first signs in.
        user = User(
            username=LEGACY_USERNAME_PREFIX + netid,
            netid=netid,
            email=row["email"] or "",
            first_name=first,
            last_name=last,
            privilege_level=PrivilegeLevel.STUDENT,
            is_staff=False,
            is_superuser=False,
        )
        user.set_unusable_password()
        return self._save(user), True

    def _create_annotation_set(self, *, name, resource, owner):
        values = {"name": name, "resource": resource, "owner": owner}
        if any(
            field.name == "retired"
            for field in AnnotationSet._meta.local_concrete_fields
        ):
            return AnnotationSet.objects.create(**values, retired=False)

        table = AnnotationSet._meta.db_table
        if self._annotation_set_has_retired_column is None:
            with connection.cursor() as cursor:
                columns = connection.introspection.get_table_description(cursor, table)
            self._annotation_set_has_retired_column = any(
                column.name == "retired" for column in columns
            )
        if not self._annotation_set_has_retired_column:
            return AnnotationSet.objects.create(**values)

        # Some target databases retain this required column after the model has
        # dropped it. Supply False without changing model or migration state.
        annotation_set = AnnotationSet(**values)
        fields = [
            field
            for field in AnnotationSet._meta.local_concrete_fields
            if not field.primary_key
        ]
        params = [
            field.get_db_prep_save(field.pre_save(annotation_set, add=True), connection)
            for field in fields
        ] + [False]
        quote = connection.ops.quote_name
        columns = ", ".join(quote(field.column) for field in fields)
        placeholders = ", ".join("%s" for _ in params)
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {quote(table)} ({columns}, {quote('retired')}) "
                f"VALUES ({placeholders})",
                params,
            )
        return AnnotationSet.objects.get(**values)

    def _course(self, row):
        department = (row["department"] or "").strip().upper()
        # Remove separate course-number tokens with their suffixes, then any
        # remaining digits, before matching complete legacy department names.
        department = re.sub(r"\b\d+[A-Z]?\b", "", department)
        department = re.sub(r"\d+", "", department)
        department_parts = department.split()
        if len(department_parts) > 1:
            department_parts = [
                part for part in department_parts if re.fullmatch(r"[A-Z&]+", part)
            ]
        department = " ".join(department_parts)
        department = _LEGACY_DEPARTMENT_ALIASES.get(department, department)
        department_parts = department.split()
        if len(department_parts) > 1 and department not in {"PHY S", "C S"}:
            if department_parts[0] == "REL":
                department = "REL " + "".join(department_parts[1:])
            else:
                department = "".join(department_parts)
        catalog_number = (row["catalog_number"] or "").strip().upper()
        # Correct numeric tokens without turning department text like SOC into S0C.
        catalog_number = re.sub(
            r"(?<![A-Z0-9])[0-9O]+[A-Z]?(?![A-Z0-9])",
            lambda match: match.group().replace("O", "0"),
            catalog_number,
        )
        catalog_match = re.search(r"\d[\dO]*[A-Z]?", catalog_number)
        if catalog_match:
            catalog_number = catalog_match.group().replace("O", "0")
        suffixed = re.fullmatch(r"(\d{1,3})([A-Z])", catalog_number)
        if suffixed:
            catalog_number = suffixed[1].zfill(3) + suffixed[2]
        else:
            catalog_number = catalog_number.zfill(3)
        values = {
            "dept": department,
            "catalog_number": catalog_number,
            "section_number": (row["section_number"] or "")
            .strip()
            .replace("O", "0")
            .zfill(3),
            "yearterm": self.yearterm,
        }
        course = Course(**values)
        try:
            course.full_clean(validate_unique=False, validate_constraints=False)
        except ValidationError as exc:
            dept = f"dept={row['department']!r}"
            if row["department"] != values["dept"]:
                dept += f" (normalized to {values['dept']!r})"
            catalog = f"catalog_number={row['catalog_number']!r}"
            if row["catalog_number"] != values["catalog_number"]:
                catalog += f" (normalized to {values['catalog_number']!r})"
            section = f"section_number={row['section_number']!r}"
            if row["section_number"] != values["section_number"]:
                section += f" (normalized to {values['section_number']!r})"
            raise BulkImportError(
                f"Course validation failed, {dept}, {catalog}, {section}: {exc}"
            ) from exc
        existing = Course.objects.filter(**values).first()
        return (existing, False) if existing else (self._save(course), True)

    def _resource(self, row):
        media_type = (row["resource_type"] or "").strip().lower()
        if media_type not in {
            "vid",
            "video",
            "aud",
            "audio",
            "txt",
            "text",
            "www",
            "web",
            "online-media",
        }:
            raise BulkImportError(f"Unknown resource_type: {media_type!r}")
        name = (row["resource_name"] or "Untitled").strip()[:255] or "Untitled"
        disambiguator = ""
        if Resource.objects.filter(name=name, name_disambiguator="").exists():
            disambiguator = f"legacy {row['id']}"
        if Resource.objects.filter(
            name=name, name_disambiguator=disambiguator
        ).exists():
            raise BulkImportError(f"Resource name/disambiguator collision: {name!r}")
        requester = ""
        email = row["requester_email"]
        if email:
            matches = [
                user
                for user in self.objects["users"].values()
                if user.email.lower() == email.lower()
            ]
            usernames = {user.username for user in matches}
            if len(usernames) == 1:
                candidate = next(iter(usernames))
                # An imported user's placeholder username changes at first login,
                # so it can't identify the requester later.
                if len(candidate) <= 9 and not candidate.startswith(
                    LEGACY_USERNAME_PREFIX
                ):
                    requester = candidate
        resource = Resource(
            name=name,
            name_disambiguator=disambiguator,
            media_type=map_legacy_media_type(media_type),
            requester_username=requester,
            copyrighted=self._boolean(row["copyrighted"], default=True),
            physical_copy_exists=self._boolean(row["physical_copy_exists"]),
            views=int(row["views"] or 0),
            notes=json.dumps(
                {"legacy_resource_id": row["id"], "legacy": row}, sort_keys=True
            ),
        )
        return self._save(resource), True

    def _collection(self, row):
        owner = self._require("users", row["owner"])
        # Legacy let staff own collections, but only instructors can own
        # playlists here.
        if owner.privilege_level_override is None and not owner.is_instructor:
            owner.privilege_level_override = PrivilegeLevel.INSTRUCTOR
            owner.save(update_fields=["privilege_level_override"])
        return self._save(
            Playlist(
                owner=owner,
                name=self._name(
                    Playlist.objects.filter(owner=owner),
                    "name",
                    row["collection_name"],
                    row["id"],
                    255,
                ),
                published=self._boolean(row["published"]),
                archived=self._boolean(row["archived"]),
            )
        ), True

    def _file(self, row):
        resource = self._require("resources", row["resource_id"])
        version = self._name(
            ResourceFile.objects.filter(resource=resource),
            "version",
            row["file_version"],
            row["id"],
            100,
        )
        source_resource = self.rows["resources"][row["resource_id"]]
        target = ResourceFile(
            resource=resource,
            version=version,
            file="",
            checksum=None,
            checksum_at=None,
            full_video=self._boolean(source_resource["full_video"], default=True),
            notes=json.dumps(
                {"legacy_file_id": row["id"], "legacy": row}, sort_keys=True
            ),
        )
        return self._save(target), True

    def _content(self, row):
        playlist = self._require("collections", row["collection_id"])
        resource_id = row["resource_id"]
        file_id = row["file_id"]
        url = (row["url"] or "").strip()
        host = (urlparse(url).hostname or "").removeprefix("www.").removeprefix("m.")
        is_youtube_url = host in {"youtube.com", "youtu.be", "youtube-nocookie.com"}
        file = None
        resource = self.objects["resources"].get(resource_id)
        if not is_youtube_url and file_id and file_id != LEGACY_URL_ONLY_RESOURCE_ID:
            file = self.objects["files"].get(file_id)
            if file is None:
                return None, False
            source_file = self.rows["files"][file_id]
            if resource_id not in (
                None,
                "",
                LEGACY_URL_ONLY_RESOURCE_ID,
                source_file["resource_id"],
            ):
                return None, False
        elif not is_youtube_url and resource is not None:
            candidates = self.source_files_by_version[
                (resource_id, row["file_version"] or "")
            ]
            if len(candidates) != 1:
                return None, False
            file = self.objects["files"].get(candidates[0]["id"])
        if file is None and not is_youtube_url:
            return None, False
        resource = file.resource if file is not None else resource
        target = self._save(
            Content(
                playlist=playlist,
                resource=resource,
                resource_file=file,
                title=self._name(
                    Content.objects.filter(playlist=playlist),
                    "title",
                    row["title"],
                    row["id"],
                    255,
                ),
                url=url or None,
                description=row["description"] or "",
                allow_definitions=self._boolean(row["allow_definitions"], default=True),
                allow_captions=self._boolean(row["allow_captions"], default=True),
                published=self._boolean(row["published"]),
                views=int(row["views"] or 0),
            )
        )
        annotations = self._events(row["annotations"], annotations=True)
        clips = self._events(row["clips"])
        if annotations or clips:
            for event in annotations:
                if annotation_model_for_type(event.get("type")) is None:
                    raise BulkImportError(
                        f"Unsupported annotation type: {event.get('type')!r}"
                    )
            for event in annotations:
                label = (
                    event.get("title") or event.get("label") or event.get("type", "")
                )
                event["title"] = str(label)[:255]
            for index, clip in enumerate(clips):
                label = (
                    clip.get("title")
                    or clip.get("label")
                    or f"{target.title} Clip {index + 1}"
                )
                clip["title"] = str(label)[:255]
            # Annotation names are unique per resource/owner, not per playlist.
            annotation_set = self._create_annotation_set(
                name=self._name(
                    AnnotationSet.objects.filter(
                        resource=target.get_resource(), owner=playlist.owner
                    ),
                    "name",
                    f"Imported {target.title} Annotations",
                    row["id"],
                    255,
                ),
                resource=target.get_resource(),
                owner=playlist.owner,
            )
            self.record("annotation_set", str(target.pk), annotation_set, {})
            import_annotations(annotation_set, target, annotations, clips)
        return target, True

    @staticmethod
    def _events(raw, annotations=False):
        if raw in (None, ""):
            return []
        try:
            events = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            if not annotations:
                raise BulkImportError("clips must contain valid JSON.") from None
            # Decode objects rather than splitting text, since comments may
            # themselves contain semicolons followed by spaces.
            events = []
            remaining = raw.strip()
            decoder = json.JSONDecoder()
            while remaining:
                event, end = decoder.raw_decode(remaining)
                events.append(event)
                remaining = remaining[end:].lstrip()
                if remaining:
                    if not remaining.startswith(";"):
                        raise BulkImportError(
                            "Expected ';' between legacy annotation objects."
                        )
                    remaining = remaining[1:].lstrip()
        if events is None:
            return []
        if isinstance(events, dict):
            events = [events]
        if not isinstance(events, list) or any(
            not isinstance(event, dict) for event in events
        ):
            raise BulkImportError(
                "Annotations/clips must be an object, a list of objects, or null."
            )
        for event in events:
            start = float(event.get("start", 0) or 0)
            end = float(event.get("end", start) or start)
            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or end < start
            ):
                raise BulkImportError(
                    "Annotation/clip times must be finite, nonnegative, and end >= start."
                )
        return events

    def _subtitle(self, row):
        content = self._require("contents", row["content_id"])
        language_code = (row["language"] or "").strip()
        languages = list(
            Language.objects.filter(
                Q(bcp47__iexact=language_code) | Q(language__iexact=language_code)
            )
        )
        if len(languages) != 1:
            raise BulkImportError(
                f"Unresolved or ambiguous subtitle language {language_code!r}; "
                "expected exactly one matching Language record."
            )
        language = languages[0]
        owner = content.playlist.owner

        target = Subtitle(
            resource=content.get_resource(),
            owner=owner,
            language=language,
            name=self._name(
                Subtitle.objects.filter(
                    resource=content.get_resource(), owner=owner, language=language
                ),
                "name",
                row["title"] or language.language,
                row["id"],
                255,
            ),
        )
        # Inline legacy cues remain in the source mapping until media is migrated.
        target.subtitles_file.name = ""
        return self._save(target), True

    def _collection_access(self, row):
        user = self._user_by_netid(row["username"])
        playlist = self._require("collections", row["collection_id"])
        role = map_legacy_collection_role(row["account_role"])
        if role is None:
            raise BulkImportError(
                f"Unknown collection account_role: {row['account_role']!r}"
            )
        target, created = PlaylistUserAccess.objects.get_or_create(
            playlist=playlist,
            user=user,
            defaults={"playlist_role": role},
        )
        if created:
            self.created_access.add(target.pk)
        elif target.pk in self.created_access:
            if role < target.playlist_role:
                target.playlist_role = role
                target.save(update_fields=["playlist_role"])
        elif target.playlist_role != role:
            raise BulkImportError(
                "Existing target playlist access has a different role; refusing to overwrite it."
            )
        return target, created

    def _resource_access(self, row):
        user = self._user_by_netid(row["username"])
        resource = self._require("resources", row["resource_id"])
        return ResourceAccess.objects.get_or_create(user=user, resource=resource)
