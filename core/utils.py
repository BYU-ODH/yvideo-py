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
