"""ffprobe / ffmpeg helpers for projection masters and their SD derivatives.

Everything here shells out with argument lists (never a shell string). The media worker
container runs at idle CPU and I/O priority, which ffmpeg inherits.
"""

from dataclasses import dataclass
from fractions import Fraction
import functools
import json
import os
from pathlib import Path
import subprocess

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

# SD editing/stream copy: 480 lines, about 1-1.5 Mbps.
SD_HEIGHT = 480
SD_THREADS = 2

ACCEPTED_CONTAINERS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}


class MediaError(Exception):
    """A file the pipeline cannot use, with a message fit for the uploader."""


@dataclass
class ProbeResult:
    duration_seconds: float | None
    width: int | None
    height: int | None
    frame_rate: float | None
    video_codec: str
    color_transfer: str
    video_start_time: float | None
    video_duration: float | None

    def apply_to(self, resource_file):
        resource_file.duration_seconds = self.duration_seconds
        resource_file.width = self.width
        resource_file.height = self.height
        resource_file.frame_rate = self.frame_rate
        resource_file.video_codec = self.video_codec
        resource_file.color_transfer = self.color_transfer

    @property
    def frame_seconds(self):
        return 1 / self.frame_rate if self.frame_rate else 1 / 24


def _parse_rate(rate):
    """ffprobe's frame rate ("24000/1001", "0/0", ...) as a float, or None."""
    try:
        return float(Fraction(rate)) or None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def probe(path):
    try:
        completed = subprocess.run(
            [
                FFPROBE,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError as exc:
        raise MediaError("ffprobe is not installed on this server.") from exc
    if completed.returncode != 0:
        raise MediaError(
            f"ffprobe could not read the file: {completed.stderr.strip()[:500]}"
        )
    data = json.loads(completed.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise MediaError("The file has no video stream.")
    format_names = set((fmt.get("format_name") or "").split(","))
    if not format_names & ACCEPTED_CONTAINERS:
        raise MediaError(
            f"Projection masters must be MP4/M4V files (got {fmt.get('format_name')})."
        )
    return ProbeResult(
        duration_seconds=_float(fmt.get("duration")),
        width=video.get("width"),
        height=video.get("height"),
        frame_rate=_parse_rate(video.get("avg_frame_rate"))
        or _parse_rate(video.get("r_frame_rate")),
        video_codec=(video.get("codec_name") or "")[:32],
        color_transfer=(video.get("color_transfer") or "")[:32],
        video_start_time=_float(video.get("start_time")),
        video_duration=_float(video.get("duration")),
    )


@functools.cache
def _passthrough_timestamps_args():
    """Keep every frame's timestamp: -fps_mode on ffmpeg >= 5.1, -vsync before that
    (developer machines may still have ffmpeg 4)."""
    try:
        help_text = subprocess.run(
            [FFMPEG, "-hide_banner", "-h", "long"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        help_text = ""
    if "-fps_mode" in help_text:
        return ["-fps_mode", "passthrough"]
    return ["-vsync", "passthrough"]


def sd_transcode_command(source, destination):
    return [
        FFMPEG,
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-progress",
        "pipe:1",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        f"scale=-2:'min({SD_HEIGHT},ih)',setsar=1",
        *_passthrough_timestamps_args(),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        "-maxrate",
        "1500k",
        "-bufsize",
        "3000k",
        "-profile:v",
        "main",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-threads",
        str(SD_THREADS),
        str(destination),
    ]


def transcode_sd(source, destination, duration_seconds=None, on_progress=None):
    """Transcode to the SD editing file, reporting progress as a 0..1 fraction."""
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        sd_transcode_command(source, destination),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for line in process.stdout:
        key, _, value = line.strip().partition("=")
        if key == "out_time_us" and duration_seconds and on_progress:
            try:
                on_progress(min(1.0, int(value) / 1_000_000 / duration_seconds))
            except ValueError:
                pass
    stderr = process.stderr.read()
    if process.wait() != 0:
        raise MediaError(f"Transcode failed: {stderr.strip()[:500]}")


def assert_same_timeline(master, derived):
    """The derived file must line up with the master to within one frame.

    Annotations are authored against the SD file and played against the master, so a
    shifted or trimmed derivative would put every skip and mute in the wrong place.
    """
    tolerance = master.frame_seconds + 0.001
    master_duration = master.video_duration or master.duration_seconds
    derived_duration = derived.video_duration or derived.duration_seconds
    if master_duration is None or derived_duration is None:
        raise MediaError("Could not read the durations needed to compare timelines.")
    if abs(master_duration - derived_duration) > tolerance:
        raise MediaError(
            f"The SD file is {derived_duration:.3f}s long but the master is "
            f"{master_duration:.3f}s; they must match to within one frame."
        )
    if (
        master.video_start_time is not None
        and derived.video_start_time is not None
        and abs(master.video_start_time - derived.video_start_time) > tolerance
    ):
        raise MediaError(
            "The SD file's first frame is not at the same time as the master's."
        )
