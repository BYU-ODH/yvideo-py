# This module shells out to `ssh`/`scp`, which requires `openssh-client` in
# the Dockerfile and the deploy user's ~/.ssh mounted into the container
# (see deploy/quadlet.container.in). Both are LEGACY MIGRATION ONLY — when
# this package is removed, delete them too. See REMOVAL.md in this
# directory for the full checklist.
import datetime
from pathlib import Path
from pathlib import PurePosixPath
import re
import shlex
import subprocess

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
import xxhash

REMOTE_LEGACY_MEDIA_ROOT_RE = re.compile(r"^(?P<host>[^:]+):(?P<path>/.*)$")


def parse_remote_legacy_path(raw_value):
    match = REMOTE_LEGACY_MEDIA_ROOT_RE.match(str(raw_value or ""))
    if not match:
        return None
    return match.group("host"), match.group("path")


def is_remote_legacy_path(raw_value):
    return parse_remote_legacy_path(raw_value) is not None


def build_remote_legacy_path(host, raw_path):
    return f"{host}:{PurePosixPath(raw_path).as_posix()}"


def resolve_legacy_file_path(legacy_path):
    raw_path = str(legacy_path)
    media_root = getattr(settings, "LEGACY_MIGRATION_MEDIA_ROOT", "")
    remote_media_root = parse_remote_legacy_path(media_root)
    if remote_media_root:
        host, root_path = remote_media_root
        path_value = PurePosixPath(raw_path)
        resolved_path = (
            path_value
            if path_value.is_absolute()
            else PurePosixPath(root_path) / path_value
        )
        return build_remote_legacy_path(host, resolved_path)

    raw_path_obj = Path(raw_path)
    if raw_path_obj.is_absolute():
        return str(raw_path_obj)
    if not media_root:
        raise ImproperlyConfigured(
            "LEGACY_MIGRATION_MEDIA_ROOT must be configured for legacy file access."
        )
    return str(Path(media_root) / raw_path_obj)


def get_legacy_file_extension(resolved_path):
    remote_path = parse_remote_legacy_path(resolved_path)
    if remote_path:
        _, path_value = remote_path
        return PurePosixPath(path_value).suffix.lower()
    return Path(resolved_path).suffix.lower()


def format_subprocess_command(command_args):
    return shlex.join([str(arg) for arg in command_args])


def build_subprocess_failure_message(
    action_label,
    target_path,
    command_args,
    return_code=None,
    stdout="",
    stderr="",
):
    message_parts = [
        f"{action_label} {target_path}.",
        f"Command: {format_subprocess_command(command_args)}.",
    ]
    if return_code is not None:
        message_parts.append(f"Exit status: {return_code}.")

    normalized_stderr = (stderr or "").strip()
    normalized_stdout = (stdout or "").strip()
    if normalized_stderr:
        message_parts.append(f"stderr: {normalized_stderr}")
    if normalized_stdout:
        message_parts.append(f"stdout: {normalized_stdout}")
    if not normalized_stderr and not normalized_stdout:
        message_parts.append("No stdout/stderr output.")

    return " ".join(message_parts)


def parse_remote_metadata_output(raw_output, resolved_path, command_args):
    normalized_output = raw_output.replace("\\t", "\t").replace("\r\n", "\n")
    normalized_output = normalized_output.replace("\n\t", "\t", 1)

    try:
        size_bytes, mtime_epoch, atime_epoch, realpath = normalized_output.split(
            "\t", 3
        )
    except ValueError as exc:
        raise OSError(
            "Unexpected metadata response for remote legacy file "
            f"{resolved_path}. Command: {format_subprocess_command(command_args)}. "
            f"Output: {raw_output!r}"
        ) from exc

    return int(size_bytes), int(mtime_epoch), int(atime_epoch), realpath.strip()


def inspect_remote_legacy_file(resolved_path):
    remote_path = parse_remote_legacy_path(resolved_path)
    if not remote_path:
        raise ValueError(f"{resolved_path} is not a remote legacy path.")

    host, path_value = remote_path
    quoted_path = shlex.quote(path_value)
    command = (
        f"resolved=$(readlink -f -- {quoted_path} 2>/dev/null "
        f"|| realpath -- {quoted_path} 2>/dev/null "
        f'|| printf "%s" {quoted_path}); '
        f"size=$(stat -Lc '%s' -- {quoted_path}) && "
        f"mtime=$(stat -Lc '%Y' -- {quoted_path}) && "
        f"atime=$(stat -Lc '%X' -- {quoted_path}) && "
        'printf "%s\\t%s\\t%s\\t%s\\n" "$size" "$mtime" "$atime" "$resolved"'
    )
    command_args = ["ssh", "-oBatchMode=yes", host, command]

    try:
        result = subprocess.run(
            command_args,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise OSError(
            build_subprocess_failure_message(
                "Could not inspect remote legacy file",
                resolved_path,
                command_args,
                return_code=exc.returncode,
                stdout=exc.stdout,
                stderr=exc.stderr,
            )
        ) from exc

    raw_output = result.stdout.rstrip("\n")
    size_bytes, mtime_epoch, atime_epoch, realpath = parse_remote_metadata_output(
        raw_output,
        resolved_path,
        command_args,
    )

    mtime_seconds = int(mtime_epoch)
    atime_seconds = int(atime_epoch)
    return {
        "absolute_path": resolved_path,
        "realpath": build_remote_legacy_path(host, realpath),
        "size_bytes": int(size_bytes),
        "device": None,
        "inode": None,
        "mtime_ns": mtime_seconds * 1_000_000_000,
        "mtime_at": datetime.datetime.fromtimestamp(mtime_seconds, tz=datetime.UTC),
        "atime_at": datetime.datetime.fromtimestamp(atime_seconds, tz=datetime.UTC),
    }


def compute_remote_checksum(resolved_path):
    remote_path = parse_remote_legacy_path(resolved_path)
    if not remote_path:
        raise ValueError(f"{resolved_path} is not a remote legacy path.")

    host, path_value = remote_path
    command = f"cat -- {shlex.quote(path_value)}"
    command_args = ["ssh", "-oBatchMode=yes", host, command]
    file_hash = xxhash.xxh64()

    with subprocess.Popen(
        command_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        if process.stdout is None or process.stderr is None:
            raise OSError(
                "Could not read remote legacy file "
                f"{resolved_path}. Command: {format_subprocess_command(command_args)}. "
                "stdout/stderr pipes were not available."
            )
        for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
            file_hash.update(chunk)
        stderr_output = process.stderr.read()
        return_code = process.wait()

    if return_code != 0:
        stderr_text = stderr_output.decode(errors="replace")
        raise OSError(
            build_subprocess_failure_message(
                "Could not read remote legacy file",
                resolved_path,
                command_args,
                return_code=return_code,
                stderr=stderr_text,
            )
        )

    return file_hash.hexdigest()


def scp_remote_legacy_file(resolved_path, destination):
    remote_path = parse_remote_legacy_path(resolved_path)
    if not remote_path:
        raise ValueError(f"{resolved_path} is not a remote legacy path.")

    host, path_value = remote_path
    # The remote side of scp interprets the path through a shell, so quote it
    # the same way inspect_remote_legacy_file does.
    remote_spec = f"{host}:{shlex.quote(path_value)}"
    command_args = ["scp", "-p", "-oBatchMode=yes", remote_spec, str(destination)]
    try:
        subprocess.run(
            command_args,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise OSError(
            build_subprocess_failure_message(
                "Could not copy remote legacy file",
                resolved_path,
                command_args,
                return_code=exc.returncode,
                stdout=exc.stdout,
                stderr=exc.stderr,
            )
        ) from exc
