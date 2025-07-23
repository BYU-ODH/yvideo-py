"""Convert between HH:MM:SS and seconds, output as JSON blur annotations.

Tab-delimited input format:

HH:MM:SS<TAB>description
MM:SS<TAB>description
...
"""

import datetime as dt
import sys
import warnings


def hms2s(time_str):
    """Convert HH:MM:SS times to seconds."""
    times = [float(i) for i in time_str.split(":")]
    size = len(times)
    if size > 3:
        raise NotImplementedError("Expected input: HH:MM:SS or MM:SS.")
    elif 1 <= size <= 3:
        padding = [0] * (3 - size)
        h, m, s = *padding, *times
    else:
        warnings.warn(f"time_str cannot be processed: {time_str}")
        return None
    return dt.timedelta(hours=h, minutes=m, seconds=s).total_seconds()


def s2hms(secs, rounding=True):
    """Convert seconds to HH:MM:SS."""
    if rounding:
        secs = round(float(secs))
    return str(dt.timedelta(seconds=secs))


if __name__ == "__main__":
    in_lines = sys.stdin.readlines()
    for line in in_lines:
        line = line.strip()
        try:
            start, desc = line.split(maxsplit=1)
        except ValueError:
            warnings.warn(f"line skipped: {line}")
            continue
        desc = desc.strip()
        seconds = hms2s(start)
        try:
            end = seconds + 10
        except ValueError:
            end = seconds
        print(
            "  {\n"
            '    "options": {\n'
            f'      "label": "{desc}  {start}",\n'
            f'      "start": "{seconds}",\n'
            f'      "end": "{end}",\n'
            '      "type": "censor",\n'
            '      "details": {\n'
            '        "type": "blur",\n'
            '        "amount": "30px",\n'
            '        "position": {\n'
            f'          "{seconds}":     [55, 79, 4, 5]\n'
            "        }\n"
            "      }\n"
            "    }\n"
            "  }\n\n"
        )
