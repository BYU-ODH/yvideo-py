def hms2seconds(hms):
    """Convert a time string in 'HH:MM:SS.SS' format to total seconds."""
    parts = hms.split(":")
    if len(parts) != 3:
        raise ValueError("Time format must be 'HH:MM:SS.SS'")
    hours, minutes, seconds = map(float, parts)
    return (hours * 3600) + (minutes * 60) + seconds


def seconds2hms(seconds):
    """Convert total seconds to a time string in 'HH:MM:SS.SS' format."""
    if seconds < 0:
        raise ValueError("Seconds cannot be negative")
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02}:{secs:05.2f}"


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
