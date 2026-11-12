from django import template

register = template.Library()

# A run fetches files for programs showing now, applies the rows, and only then fetches
# films for later programs (ic.sync.agent.execute_run).
PHASES = [
    ("contacting", "Contacting production"),
    ("downloading", "Downloading current films"),
    ("importing", "Importing"),
    ("verifying", "Verifying"),
    ("downloading_upcoming", "Downloading upcoming films"),
    ("reporting", "Reporting"),
]
DOWNLOAD_PHASES = {"downloading", "downloading_upcoming"}
FINAL = {"succeeded", "partial", "failed", "up_to_date"}


@register.filter
def filesize_gb(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0 GB"
    if value < 1e9:
        return f"{value / 1e6:.0f} MB"
    return f"{value / 1e9:.1f} GB"


@register.inclusion_tag("ic/partials/sync_phases.html")
def sync_phases(run):
    """A phase stepper for an ICSyncRun or the booth's progress JSON for one."""
    get = (
        run.get
        if isinstance(run, dict)
        else lambda key, default=None: getattr(run, key, default)
    )
    phase = get("phase") or ""
    status = get("status") or ""
    names = [name for name, _ in PHASES]
    current = (
        names.index(phase)
        if phase in names
        else (len(names) if status in FINAL else -1)
    )
    steps = []
    for index, (name, label) in enumerate(PHASES):
        if index < current:
            css = "ic-phase-done"
        elif index == current:
            css = "ic-phase-current"
        else:
            css = "ic-phase-todo"
        steps.append({"name": name, "label": label, "css": css})
    return {
        "steps": steps,
        "phase": phase,
        "downloading": phase in DOWNLOAD_PHASES,
        "files_done": get("files_done", 0),
        "files_total": get("files_total", 0),
        "bytes_done": get("bytes_done", 0),
        "bytes_total": get("bytes_total", 0),
        "failed": status == "failed",
    }


@register.filter
def get_item(mapping, key):
    if not isinstance(mapping, dict):
        return None
    return mapping.get(key, mapping.get(str(key)))
