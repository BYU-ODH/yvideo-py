"""Subtitle parsing, validation, serialization, and file storage helpers."""

from decimal import ROUND_HALF_UP
from decimal import Decimal
from decimal import InvalidOperation
import json
import logging
from math import isfinite
from os.path import realpath
from os.path import splitext
from pathlib import PureWindowsPath
import re
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db.models import Q

logger = logging.getLogger(__name__)

MAX_SUBTITLE_UPLOAD_BYTES = 5 * 1024 * 1024
SUBTITLE_FILE_FIELDS = ("subtitles_file", "subtitles_temp_file")
_TIMESTAMP = r"(?:[0-9]+:)?[0-5][0-9]:[0-5][0-9][.,][0-9]{1,3}"
_TIMING_LINE = re.compile(
    rf"[ \t]*(?P<start>{_TIMESTAMP})[ \t]+-->[ \t]+(?P<end>{_TIMESTAMP})"
    r"(?P<settings>(?:[ \t]+[^\n]*)?)"
)
_METADATA_PREFIXES = ("NOTE", "STYLE", "REGION")


def _timestamp_to_milliseconds(timestamp):
    """_timestamp_to_milliseconds
    Converts a timestamp string into milliseconds. Returns False if an error occurs while parsing.
    """
    if not isinstance(timestamp, str):
        logger.warn(
            f"_timestamp_to_milliseconds only accepts a string got {type(timestamp)}"
        )
        return False

    # subtitle timestamps need to be like this: 00:00:00.000
    parts = timestamp.strip().replace(",", ".").split(":")
    if 1 <= len(parts) <= 3 and all(parts):
        seconds, _, fraction = parts[-1].partition(".")
        if seconds and len(fraction) <= 3:
            parts[-1] = f"{seconds.zfill(2)}.{fraction.ljust(3, '0')}"
            parts = ["00"] * (3 - len(parts)) + parts
            timestamp = ":".join(part.zfill(2) for part in parts)

    parts = timestamp.replace(",", ".").split(":")
    seconds, fraction = parts[-1].split(".")

    try:
        hours = int(parts[0]) if len(parts) == 3 else 0
        minutes = int(parts[-2])
        return ((hours * 60 + minutes) * 60 + int(seconds)) * 1000 + int(
            fraction.ljust(3, "0")
        )
    except ValueError:
        logger.warn(
            f"_timestamp_to_milliseconds encountered invalid subtitle timestamp: {timestamp}"
        )
        return False


def _seconds_to_milliseconds(value):
    """_seconds_to_milliseconds
    Converts timestamps and numeric seconds to milliseconds. Returns False if there is an error.
    """
    if isinstance(value, str):
        seconds = _timestamp_to_milliseconds(value)
        return _timestamp_to_milliseconds(value)
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        logger.warn(
            f"_seconds_to_milliseconds input must be timestamps or numeric seconds. Got: {value} which is type: {type(value)}."
        )
        return False
    try:
        seconds = Decimal(str(value))
        if seconds < 0:
            seconds = 0
        if not seconds.is_finite():
            logger.warn("_seconds_to_milliseconds input must be finite")
            return False
        return int((seconds * 1000).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, OverflowError):
        logger.warn(
            "_seconds_to_milliseconds was provided an invalid subtitle timestamp."
        )
        return False


def _milliseconds_to_seconds(milliseconds):
    """_milliseconds_to_seconds
    Converts milliseconds to seconds. Returns False if there is an error while parsing.
    """
    try:
        seconds = milliseconds / 1000
        if not isfinite(seconds):
            raise OverflowError
        return seconds
    except Exception:
        logger.warn("_milliseconds_to_seconds was provided an invalid input.")
        return False


def _subtitle_seconds(value):
    return _milliseconds_to_seconds(_seconds_to_milliseconds(value))


def format_subtitle_timestamp(seconds):
    """Format subtitle times independently of the annotation editor's hundredths."""
    milliseconds = _seconds_to_milliseconds(seconds)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def _subtitle_lines(text):
    if not isinstance(text, str):
        raise ValidationError("Subtitle contents must be text.")
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in text:
        raise ValidationError("Subtitle files cannot contain null characters.")

    # Normalize separators without stripping whitespace from meaningful cue text.
    lines = []
    for line in text.split("\n"):
        if line.strip() == "":
            lines.append("")
        else:
            lines.append(line)
    return lines


class VTTCue:
    """VTTCue
    VTT cues have a set of standard features; we don't support most of them. We will support
    start and end times (using VTT timing notation), the CUE type, and the subtitle text
    payload.
    """

    def __init__(
        self, type=None, payload="", start_time=None, end_time=None, is_valid=True
    ):
        self.type = type
        self.payload = payload

        # since 0 is a valid seconds value but is considered falsey by Python, we have to be
        # explicit with our conditional expressions here
        parsed_start_time = _subtitle_seconds(start_time)
        if parsed_start_time is not None and parsed_start_time is not False:
            self.start_time = parsed_start_time

        parsed_end_time = _subtitle_seconds(end_time)
        if parsed_end_time is not None and parsed_end_time is not False:
            self.end_time = parsed_end_time

    def validate(self):
        # We will only support CUE type VTT cues
        if self.type != "CUE":
            self.is_valid = False

        start = _seconds_to_milliseconds(self.start_time)
        end = _seconds_to_milliseconds(self.end_time)
        if end <= start:
            self.is_valid = False

        # The payload can't contain null character or the arrow required in the cue timing line
        # because this will mess up the VTT parser
        if (
            not isinstance(self.payload, str)
            or "\x00" in self.payload
            or "-->" in self.payload
        ):
            self.is_valid = False

        self.payload = self.payload.strip()
        self.payload = self.payload.replace("\r\n", "\n").replace("\r", "\n")
        if self.payload == "":
            self.is_valid = False
        elif any(not line.strip() for line in self.payload.split("\n")):
            self.payload = self.payload.replace("\n", " ")

        parsed_start_time = _subtitle_seconds(self.start_time)
        if parsed_start_time is None or parsed_start_time is False:
            self.is_valid = False

        parsed_end_time = _subtitle_seconds(self.end_time)
        if parsed_end_time is None or parsed_end_time is False:
            self.is_valid = False

        self.start_time = parsed_start_time
        self.end_time = parsed_end_time

    def from_json_dict(self, data):
        if not isinstance(data, dict):
            raise ValidationError("Each subtitle cue must be an object.")
        self.__init__(
            type=data.get("type"),
            payload=data.get("payload", ""),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
        )
        self.validate()

    def from_string(self, text):
        if not isinstance(text, str):
            raise ValidationError("Subtitle contents must be text.")
        cues = parse_subtitle_text("WEBVTT\n\n" + text)
        if len(cues) != 1:
            raise ValidationError("Expected exactly one timed subtitle cue.")
        self.__dict__.update(cues[0].__dict__)

    def to_string(self):
        self.validate()
        lines = []
        timing = (
            f"{format_subtitle_timestamp(self.start_time)} --> "
            f"{format_subtitle_timestamp(self.end_time)}"
        )
        lines.extend([timing, self.payload])
        return "\n".join(lines)

    def display_start_time(self):
        return format_subtitle_timestamp(self.start_time)

    def display_end_time(self):
        return format_subtitle_timestamp(self.end_time)

    def validate_nudge(self, seconds_nudge):
        # // finish this validation
        try:
            offset = Decimal(str(seconds_nudge))
            if not offset.is_finite():
                raise InvalidOperation
            start = Decimal(str(self.start_time)) + offset
            end = Decimal(str(self.end_time)) + offset
            start_is_valid = start.is_finite()
            end_is_valid = end.is_finite()
        except (InvalidOperation, ValueError) as exception:
            logger.error(
                f"Failed to nudge cue times due to invalid input: {seconds_nudge}"
            )
            raise ValidationError(
                "The subtitle time offset must be finite numeric seconds."
            ) from exception
        return start_is_valid and end_is_valid

    def nudge_times(self, seconds_nudge):
        # // finish this function
        offset = Decimal(str(seconds_nudge))

        # we don't need to check for False values from _subtitle_seconds here because
        # the result should already have been validated using validate_nudge
        start = Decimal(str(self.start_time)) + offset
        end = Decimal(str(self.end_time)) + offset
        self.start_time = _subtitle_seconds(start)
        self.end_time = _subtitle_seconds(end)
        self.validate()


def _parse_subtitle_block_into_VTTCue(block):
    """_parse_subtitle_block_into_VTTCue
    Parses blocks that are believed to be cues. If the block can't be parsed as a CUE,
    the function returns False signifying that the block should not be included in
    the VTT file.
    """
    timing_offset = 0 if "-->" in block[0] else 1
    if len(block) <= timing_offset:
        return False
    timing = _TIMING_LINE.fullmatch(block[timing_offset])
    if timing is None:
        return False
    start_time = _milliseconds_to_seconds(
        _timestamp_to_milliseconds(timing.group("start"))
    )
    end_time = _milliseconds_to_seconds(_timestamp_to_milliseconds(timing.group("end")))
    if (
        isinstance(start_time, bool)
        and start_time is False
        or isinstance(end_time, bool)
        and end_time is False
    ):
        return False
    cue = VTTCue(
        type="CUE",
        payload="\n".join(block[timing_offset + 1 :]),
        start_time=start_time,
        end_time=end_time,
    )
    cue.validate()
    return cue


def parse_subtitle_text(text, *, file_format="vtt"):
    """Parse VTT/SRT with shared validation; empty tracks and blank timed cues are valid."""
    if file_format not in ("vtt", "srt"):
        raise ValidationError("Subtitles must use VTT or SRT format.")
    lines = _subtitle_lines(text)
    position = 0
    if file_format == "vtt":
        if (
            not re.fullmatch(r"WEBVTT(?:[ \t][^\n]*)?", lines[0])
            or "-->" in lines[0]
            or len(lines) < 2
            or lines[1]
        ):
            raise ValidationError(
                "WebVTT files must start with WEBVTT and a blank line."
            )
        position = 2

    cues = []
    while position < len(lines):
        if not lines[position]:
            position += 1
            continue
        block_start = position
        while position < len(lines) and lines[position]:
            position += 1
        block = lines[block_start:position]
        if block[0].startswith(_METADATA_PREFIXES):
            continue
        parsed_cue = _parse_subtitle_block_into_VTTCue(block)
        if parsed_cue and parsed_cue.is_valid:
            cues.append(parsed_cue)
    return cues


def build_cues_from_vtt_file_string(text):
    """Read stored VTT, including the timestamp format used by the older editor."""
    return parse_subtitle_text(text)


def build_vtt_file_string_from_cues(cues):
    return "WEBVTT\n\n" + "\n\n".join(cue.to_string() for cue in cues)


def build_subtitle_vtt(raw_content):
    """Convert legacy JSON cue data using the same validation and VTT serializer."""
    if raw_content is None or raw_content == "":
        raw_content = []
    elif isinstance(raw_content, str):
        try:
            raw_content = json.loads(raw_content)
        except json.JSONDecodeError as exception:
            raise ValidationError(
                "Legacy subtitle content must be valid JSON."
            ) from exception
    if not isinstance(raw_content, list):
        raise ValidationError("Legacy subtitle content must be a list of cues.")

    cues = []
    for data in raw_content:
        if not isinstance(data, dict):
            raise ValidationError("Each legacy subtitle cue must be an object.")
        try:
            start = data.get("start", 0) or 0
            end = data.get("end", 0) or 0
            if isinstance(start, bool) or isinstance(end, bool):
                raise ValueError
            cue = VTTCue(
                type="CUE",
                payload=data.get("text", ""),
                start_time=float(start),
                end_time=float(end),
            )
        except (TypeError, ValueError, OverflowError) as exception:
            raise ValidationError(
                "Legacy subtitle times must be numeric seconds."
            ) from exception
        cues.append(cue)
    return build_vtt_file_string_from_cues(cues)


def convert_srt_content_to_vtt(text):
    return build_vtt_file_string_from_cues(parse_subtitle_text(text, file_format="srt"))


def _decode_subtitle_bytes(data):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exception:
        raise ValidationError("Subtitle files must use UTF-8 encoding.") from exception


def read_subtitle_cues(file, *, file_format="vtt"):
    """Read stored subtitle bytes, allowing the older editor's timestamp format."""
    return parse_subtitle_text(
        _decode_subtitle_bytes(file.read()),
        file_format=file_format,
    )


def generate_vtt_cues_from_file_path(path):
    with open(path, "rb") as file:
        return read_subtitle_cues(file)


def nudge_cue_times(cues, nudge_excluded_cues, seconds_nudge):
    # validate that the nudge will not result in an error for any
    # cue. If it is invalid for one of them, we don't run it for any of them.
    for index, cue in enumerate(cues):
        if index not in nudge_excluded_cues:
            validation_check = cue.validate_nudge(seconds_nudge)
            if validation_check is False:
                return False

    for index, cue in enumerate(cues):
        if index not in nudge_excluded_cues:
            cue.nudge_times(seconds_nudge)


def validate_subtitle_file(file):
    """Keep the model's extension validator importable by historical migrations."""
    if splitext(file.name)[1].lower() not in (".vtt", ".srt"):
        raise ValidationError("Upload a .vtt or .srt subtitle file.")


def normalize_subtitle_upload(upload):
    """Validate the upload boundary, then use the shared parser and VTT serializer."""
    validate_subtitle_file(upload)
    if upload.size > MAX_SUBTITLE_UPLOAD_BYTES:
        raise ValidationError("Subtitle files must be no larger than 5 MiB.")
    upload.seek(0)
    data = upload.read(MAX_SUBTITLE_UPLOAD_BYTES + 1)
    if len(data) > MAX_SUBTITLE_UPLOAD_BYTES:
        raise ValidationError("Subtitle files must be no larger than 5 MiB.")
    cues = parse_subtitle_text(
        _decode_subtitle_bytes(data), file_format=splitext(upload.name)[1].lower()[1:]
    )
    return build_vtt_file_string_from_cues(cues).encode("utf-8")


def build_subtitle_file_from_upload(upload):
    content = normalize_subtitle_upload(upload)
    # Recognize both Windows and POSIX separators, regardless of the OS.
    filename = PureWindowsPath(upload.name).with_suffix(".vtt").name
    return ContentFile(content, name=filename)


def _same_storage(first, second):
    if first is second or first == second:
        return True
    if isinstance(first, FileSystemStorage) and isinstance(second, FileSystemStorage):
        return realpath(first.location) == realpath(second.location)
    return False


def _subtitle_file_is_referenced(storage, name, using=None):
    from .models import Subtitle

    references = Q()
    for field_name in SUBTITLE_FILE_FIELDS:
        field = Subtitle._meta.get_field(field_name)
        if _same_storage(field.storage, storage):
            references |= Q(**{field_name: name})
    return (
        bool(references) and Subtitle.objects.using(using).filter(references).exists()
    )


def subtitle_file_references(subtitle):
    return [
        (file.storage, file.name)
        for field_name in SUBTITLE_FILE_FIELDS
        if (file := getattr(subtitle, field_name)) and file.name
    ]


def store_independent_subtitle_file(
    subtitle, content, created_files, extension=".vtt", *, field_name="subtitles_file"
):
    """Save to the selected field's storage and track attempted writes for cleanup."""
    from .models import Subtitle

    field = Subtitle._meta.get_field(field_name)
    storage = field.storage
    path_field = Subtitle._meta.get_field("subtitles_file")
    while True:
        name = path_field.generate_filename(subtitle, f"{uuid4().hex}{extension}")
        if not storage.exists(name) and not _subtitle_file_is_referenced(
            storage, name, using=subtitle._state.db
        ):
            break
    created_files.append((storage, name))
    content.seek(0)
    saved_name = storage.save(name, content, max_length=field.max_length)
    if saved_name != name:
        created_files.append((storage, saved_name))
    return saved_name


def cleanup_subtitle_files(files, using=None):
    """Delete only unreferenced files; cleanup failure must not undo a committed edit."""
    deleted = []
    for storage, name in files:
        if not name or any(
            old_name == name and _same_storage(old_storage, storage)
            for old_storage, old_name in deleted
        ):
            continue
        try:
            if not _subtitle_file_is_referenced(storage, name, using=using):
                storage.delete(name)
                deleted.append((storage, name))
        except Exception:
            logger.exception("Failed to clean up subtitle file %s.", name)
