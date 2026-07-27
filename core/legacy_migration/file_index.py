from collections import defaultdict
from pathlib import Path

import xxhash

from ..models import ResourceFile
from .remote_files import compute_remote_checksum
from .remote_files import is_remote_legacy_path
from .remote_files import parse_remote_legacy_path


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


class CurrentFileIndex:
    def __init__(self, checksum_cache):
        self.checksum_cache = checksum_cache
        self.by_realpath = defaultdict(list)
        self.by_inode = defaultdict(list)
        self.by_size = defaultdict(list)
        self.by_pk = {}
        self._load()

    def _load(self):
        resource_files = list(ResourceFile.objects.select_related("resource"))
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

        if legacy_file_info.realpath:
            append_matches(
                self.by_realpath.get(legacy_file_info.realpath, []), "same_realpath"
            )

        if legacy_file_info.device is not None and legacy_file_info.inode is not None:
            append_matches(
                self.by_inode.get(
                    (legacy_file_info.device, legacy_file_info.inode), []
                ),
                "same_device_inode",
            )

        if not candidates and legacy_file_info.size_bytes is not None:
            source_checksum = self.checksum_cache.get_or_compute_legacy_checksum(
                legacy_file_info
            )
            if source_checksum:
                for entry in self.by_size.get(legacy_file_info.size_bytes, []):
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
        absolute_path = legacy_file_info.absolute_path
        if is_remote_legacy_path(absolute_path):
            host, path_value = parse_remote_legacy_path(absolute_path)
            key = (
                "remote",
                host,
                path_value,
                legacy_file_info.size_bytes,
                legacy_file_info.mtime_ns,
            )
            if key not in self.cache:
                self.cache[key] = compute_remote_checksum(absolute_path)
            return self.cache[key]
        try:
            path_obj = Path(absolute_path)
            key = (
                legacy_file_info.device,
                legacy_file_info.inode,
                legacy_file_info.size_bytes,
                legacy_file_info.mtime_ns,
            )
            if key not in self.cache:
                self.cache[key] = compute_checksum(path_obj)
            return self.cache[key]
        except OSError:
            return ""
