from re import findall
from re import sub

from django.utils import timezone


def estimate_current_yearterm(today=None):
    """Estimate the current BYU yearterm code (e.g. "20264") from the date.

    Term cutoffs approximate the BYU academic calendar
    (https://academiccalendar.byu.edu/), whose exact dates shift a few days
    each year (2026: Winter Jan 5 - Apr 15, Spring Apr 27 - Jun 15,
    Summer Jun 22 - Aug 10, Fall Sep 2 - Dec 10). Days in the gap between
    two terms are split at the midpoint, except that the winter break through
    December belongs to Fall. For the authoritative answer, use
    Api.get_current_year_term, which requires a BYU API credential.
    """
    if today is None:
        today = timezone.localdate()
    if (today.month, today.day) >= (8, 22):
        term = "5"  # Fall
    elif (today.month, today.day) >= (6, 19):
        term = "4"  # Summer
    elif (today.month, today.day) >= (4, 22):
        term = "3"  # Spring
    else:
        term = "1"  # Winter
    return f"{today.year}{term}"


def hms2seconds(hms):
    """Convert a time string in 'HH:MM:SS.SS' format to total seconds."""
    parts = hms.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = map(float, parts)
        return (hours * 3600) + (minutes * 60) + seconds
    elif len(parts) == 2:
        minutes, seconds = map(float, parts)
        return (minutes * 60) + seconds
    else:
        raise ValueError("Time format must be 'HH:MM:SS.SS'")


def seconds2hms(seconds):
    """Convert total seconds to a time string in 'HH:MM:SS.SSS' format."""
    if seconds < 0:
        raise ValueError("Seconds cannot be negative")

    seconds = round(seconds, 2)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - (hours * 3600 + minutes * 60)
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def convert_srt_content_to_vtt(srt_string):
    def swap_comma_for_period(match):
        return match[0].replace(",", ".")

    new_content = "WEBVTT\n\n" + sub(r",[0-9]{3}", swap_comma_for_period, srt_string)
    new_content = new_content.replace("\r", "")
    new_content = new_content.replace("\ufeff", "")
    return new_content


class VTTCue:
    def __init__(
        self,
        type=None,
        payload=None,
        identifier=None,
        start_time=None,
        end_time=None,
        cue_settings=None,
    ):
        self.type = type
        self.payload = payload
        self.identifier = identifier
        self.start_time = (
            hms2seconds(start_time) if isinstance(start_time, str) else start_time
        )
        self.end_time = hms2seconds(end_time) if isinstance(end_time, str) else end_time
        self.cue_settings = None

    def from_json_dict(self, json_dict):
        self.type = json_dict["type"]
        self.payload = json_dict["payload"]
        self.identifier = json_dict["identifier"]
        self.start_time = hms2seconds(json_dict["start_time"])
        self.end_time = hms2seconds(json_dict["end_time"])
        self.cue_settings = json_dict["cue_settings"]

    def from_string(self, vtt_cue_string):
        if not isinstance(vtt_cue_string, str):
            return None

        lines = [line for line in vtt_cue_string.split("\n")]

        # find the header line. Some cues have identifiers before heading line
        header_line_index = 0
        for line in lines:
            if line.startswith("STYLE"):
                self.type = "STYLE"
                break
            elif line.startswith("NOTE"):
                self.type = "NOTE"
                note_inline_payload = findall(r"NOTE (.*)", line)
                if note_inline_payload:
                    self.payload = note_inline_payload[0]
                break
            elif line.startswith("REGION"):
                # Region types only have payload, so all we need to do for them is assign the type and let the payload
                # compiling part of this method take care of the rest.
                self.type = "REGION"
                break
            elif "-->" in line:
                self.type = "CUE"
                if header_line_index == 1:
                    self.identifier = lines[0]
                time_matches = findall(r"(\d*:?\d{1,2}:\d{1,2}.\d{1,3})", line)
                self.start_time = hms2seconds(time_matches[0])
                self.end_time = hms2seconds(time_matches[1])
                cue_settings_matches = findall(r"\s([^-->].*)", line)
                if cue_settings_matches:
                    self.cue_settings = cue_settings_matches[0]
                break
            header_line_index += 1

        payload_start_index = header_line_index + 1
        if self.payload is None:
            self.payload = ""
        for line in lines[payload_start_index:]:
            if line == "":
                continue

            if len(self.payload) > 0:
                self.payload += "\n" + line
            else:
                self.payload += line

    def to_string(self) -> str:
        if self.type is None or self.payload is None:
            return ""
        vtt_string = ""
        if self.identifier:
            vtt_string += self.identifier.strip() + "\n"
        if self.type == "STYLE" or self.type == "NOTE" or self.type == "REGION":
            vtt_string += self.type.strip() + "\n"
        elif self.type == "CUE":
            vtt_string += (
                f"{seconds2hms(self.start_time)} --> {seconds2hms(self.end_time)}"
            )
            if self.cue_settings is not None:
                vtt_string += f" {self.cue_settings}"
            vtt_string += "\n"
        vtt_string += self.payload.strip()
        return vtt_string

    def display_start_time(self):
        return f"{seconds2hms(self.start_time)}"

    def display_end_time(self):
        return f"{seconds2hms(self.end_time)}"

    def nudge_times(self, seconds_nudge: int):
        if self.start_time is not None:
            self.start_time += seconds_nudge

        if self.end_time is not None:
            self.end_time += seconds_nudge


def build_vtt_file_string_from_cues(cues: list[VTTCue]) -> str:
    vtt_string = "WEBVTT\n\n"
    cue_index = 0
    cue_count = len(cues)
    for cue in cues:
        vtt_string += cue.to_string()
        cue_index += 1
        if cue_index < cue_count:
            vtt_string += "\n\n"
    return vtt_string


def build_cues_from_vtt_file_string(vtt_string: str) -> list[VTTCue]:
    cue_list: list[VTTCue] = []
    cue_string = ""

    def add_cue_to_list_if_string_is_not_empty():
        nonlocal cue_string
        if cue_string != "":
            new_cue = VTTCue()
            new_cue.from_string(cue_string)
            cue_list.append(new_cue)
            cue_string = ""

    lines = [line for line in vtt_string.split("\n")]
    line_count = len(lines)
    line_index = 0
    for line in lines:
        line_index += 1
        if line == "WEBVTT" or line == "":
            add_cue_to_list_if_string_is_not_empty()
        elif line_index < line_count:
            cue_string += line + "\n"
        else:
            cue_string += line

    # check for a left over cue
    if cue_string != "":
        add_cue_to_list_if_string_is_not_empty()

    return cue_list


def generate_vtt_cues_from_file_path(vtt_file_path: str) -> list[VTTCue]:
    with open(vtt_file_path) as f:
        vtt_str = f.read()
        cues = build_cues_from_vtt_file_string(vtt_str)
        return cues


def nudge_cue_times(
    cue_list: list[VTTCue], nudge_excluded_cues: list[int], seconds_nudge: int
):
    """Moves the start and end time of the cue by the amount provided in seconds_nudge.
    All cues whose index is in nudge_excluded_cues will not be nudged."""
    for cue_index in range(0, len(cue_list)):
        cue = cue_list[cue_index]
        if cue.type != "CUE":
            continue
        if cue_index not in nudge_excluded_cues:
            cue.nudge_times(seconds_nudge)
