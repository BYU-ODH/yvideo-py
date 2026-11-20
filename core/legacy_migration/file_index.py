import xxhash

from .io_limits import read_chunks


def compute_checksum(path_obj):
    file_hash = xxhash.xxh64()
    with open(path_obj, "rb") as handle:
        for chunk in read_chunks(handle):
            file_hash.update(chunk)
    return file_hash.hexdigest()
