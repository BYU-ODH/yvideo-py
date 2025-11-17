from re import findall
from re import sub

from django.core.files.base import ContentFile


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
    """Convert total seconds to a time string in 'HH:MM:SS.SS' format."""
    if seconds < 0:
        raise ValueError("Seconds cannot be negative")
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02}:{secs:05.2f}"


def convert_srt_content_to_vtt(srt_file):
    def swap_comma_for_period(match):
        return match[0].replace(",", ".")

    with open(srt_file) as srt_file:
        return "WEBVTT\n\n" + sub(r",[0-9]{3}", swap_comma_for_period, srt_file.read())


def convert_srt_to_vtt_or_return_original(content_file):
    file_name_split = content_file.name.split(".")
    file_ext = file_name_split[len(file_name_split) - 1]
    if file_ext == "srt":
        vtt_content = convert_srt_content_to_vtt(content_file)
        new_file_name = file_name_split[0] + ".vtt"
        return ContentFile(content=vtt_content, name=new_file_name)
    else:
        return content_file


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
        self, type=None, payload=None, identifier=None, startTime=None, endTime=None
    ):
        self.type = type
        self.payload = payload
        self.identifier = identifier
        self.startTime = startTime
        self.endTime = endTime

    def from_string(self, vtt_string):
        if not isinstance(vtt_string, str):
            return None

        lines = [line for line in vtt_string.split("\n")]
        lines = [line for line in lines if line]  # Remove empty lines

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
                # I'm not sure we need to support REGION tags - BDR
                self.type = "REGION"
                break
            elif "-->" in line:
                self.type = "CUE"
                if header_line_index == 1:
                    self.identifier = lines[0]
                time_matches = findall(r"(\d*:?\d{1,2}:\d{1,2}.\d{1,3})", line)
                self.startTime = hms2seconds(time_matches[0])
                self.endTime = hms2seconds(time_matches[1])
                break
            header_line_index += 1

        payload_start_index = header_line_index + 1
        if self.payload is None:
            self.payload = ""
        for line in lines[payload_start_index:]:
            self.payload += line

    def to_string(self):
        if self.type is None or self.payload is None:
            return ""
        vtt_string = ""
        if self.identifier:
            vtt_string += self.identifier.strip() + "\n"
        if self.type == "STYLE" or self.type == "NOTE" or self.type == "REGION":
            vtt_string += self.type.strip() + "\n"
        elif self.type == "CUE":
            vtt_string += (
                f"{seconds2hms(self.startTime)} --> {seconds2hms(self.endTime)}\n"
            )

        vtt_string += self.payload.strip()
        return vtt_string
