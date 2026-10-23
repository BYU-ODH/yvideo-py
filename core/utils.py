from django.utils import timezone

# Map annotation type strings to their timeline/panel icon's static path.
ANNOTATION_ICONS = {
    "skip": "img/skip-icon.svg",
    "mute": "img/mute-icon.svg",
    "blank": "img/blank-icon.svg",
    "pause": "img/pause-icon.svg",
    "blur": "img/blur-icon.svg",
    "comment": "img/comment-icon.svg",
    "clip": "img/clip-icon.svg",
}


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


def time2seconds(value):
    """Seconds from a time as the editor sends it: 'H:MM:SS.SS' from a field, a number from a drag.

    The editor's time fields show what seconds2hms wrote and post back what they show, while the
    timeline sends the numbers it works in. Mirrors parseTimeStringToSeconds in static/js/utils.js.
    """
    if isinstance(value, str) and ":" in value:
        return hms2seconds(value)
    return float(value)


def seconds2hms(seconds):
    """Convert total seconds to a time string in 'HH:MM:SS.SSS' format."""
    if seconds < 0:
        raise ValueError("Seconds cannot be negative")

    seconds = round(seconds, 2)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - (hours * 3600 + minutes * 60)
    return f"{hours}:{minutes:02d}:{secs:05.2f}"
