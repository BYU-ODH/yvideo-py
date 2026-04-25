from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from .models import LegacyMigrationKind
from .parsers import LEGACY_URL_ONLY_RESOURCE_ID


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
                uca.username AS username,
                u.id AS legacy_user_id,
                u.byu_person_id,
                u.email
            FROM user_collections_assoc uca
            LEFT JOIN users u ON u.username = uca.username
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
