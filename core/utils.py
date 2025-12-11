from re import findall
from re import sub

from django.template.loader import render_to_string


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
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02}:{minutes:02}:{secs:06.3f}"


def convert_srt_content_to_vtt(srt_string):
    def swap_comma_for_period(match):
        return match[0].replace(",", ".")

    new_content = "WEBVTT\n\n" + sub(r",[0-9]{3}", swap_comma_for_period, srt_string)
    new_content = new_content.replace("\r", "")
    new_content = new_content.replace("\ufeff", "")
    return new_content


# TODO : Remove these toy VTTs after testing is complete
TOY_VTT = """WEBVTT

00:00.000 --> 00:00.900
Hildy!

00:01.000 --> 00:01.400
How are you?

00:01.500 --> 00:02.900
Tell me, is the lord of the universe in?

00:03.000 --> 00:04.200
Yes, he's in - in a bad humor

00:04.300 --> 00:06.000
Somebody must've stolen the crown jewels"""

TOY_VTT2 = """WEBVTT

00:00.000 --> 00:00.900
Birds!

00:01.000 --> 00:01.400
Where are they?

00:01.500 --> 00:02.900
You don't know?

00:03.000 --> 00:04.200
Yes, but I want to know if you do.

00:04.300 --> 00:06.000
Oh, well I know too, so we don't have to say.

00:06.000 --> 00:06.900
Look outside!

00:07.000 --> 00:07.900
They're flying everywhere.

00:08.000 --> 00:08.900
Did you see the blue one?

00:09.000 --> 00:12.900
Yes, it landed on the fence.

00:10.000 --> 00:10.900
What about the red one?

00:11.000 --> 00:11.900
It was chasing the yellow.

00:12.000 --> 00:12.900
The flock is growing.

00:13.000 --> 00:13.900
They're singing loudly.

00:14.000 --> 00:14.900
Do you hear that melody?

00:15.000 --> 00:15.900
It's beautiful, isn't it?

00:16.000 --> 00:16.900
They must be happy.

00:17.000 --> 00:17.900
The sun is shining.

00:18.000 --> 00:18.900
Perfect day for birds.

00:19.000 --> 00:19.900
Let's watch them together.

00:20.000 --> 00:20.900
Maybe they'll come closer.
"""


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
        self.identfier = json_dict["identifier"]
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

    lines = [line for line in vtt_string.split("\n")]
    for line in lines:
        if line == "WEBVTT" or line == "":
            add_cue_to_list_if_string_is_not_empty()
        else:
            cue_string += line

    # check for a left over cue
    if cue_string != "":
        add_cue_to_list_if_string_is_not_empty()

    return cue_list


def generate_vtt_cues_html_from_file_path(vtt_file_path: str) -> str:
    with open(vtt_file_path) as f:
        vtt_str = f.read()
        cues = build_cues_from_vtt_file_string(vtt_str)
        return render_to_string("partials/vtt_cues.html", {"cues": cues})


def nudge_cue_times(
    cue_list: list[VTTCue], nudge_excluded_cues: list[int], seconds_nudge: int
):
    """Moves the start and end time of the cue by the amount provided in seconds_nudge.
    All cues whose index is in nudge_excluded_cues will not be nudged."""
    cue_index = 0
    for cue in cue_list:
        if cue.type != "CUE":
            continue
        if cue_index not in nudge_excluded_cues:
            cue.nudge_times(seconds_nudge)
        cue_index += 1
