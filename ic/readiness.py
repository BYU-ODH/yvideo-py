"""Whether a program is ready to show.

Errors block "ready"; warnings are shown but do not.
"""

from dataclasses import dataclass

from django.conf import settings

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    severity: str
    code: str
    message: str

    @property
    def is_error(self):
        return self.severity == ERROR


def _verified_on_a_booth(master):
    from .models import ICInstanceFile

    return any(
        copy.matches()
        for copy in ICInstanceFile.objects.filter(resource_file=master).select_related(
            "resource_file"
        )
    )


def master_problems(master):
    problems = []

    def add(severity, code, message):
        problems.append(Problem(severity, code, message))

    codec = (master.video_codec or "").lower()
    allowed = [c.lower() for c in settings.IC_BOOTH_VIDEO_CODECS]
    if codec not in allowed:
        add(
            ERROR,
            "codec",
            f"The booth hasn't been verified to decode {codec or 'this codec'} "
            f"(verified: {', '.join(allowed)}). Use an H.264 master.",
        )
    if master.is_hdr:
        add(
            WARNING,
            "hdr",
            "HDR master: Chrome on Linux won't tone-map it for the projector, so it will "
            "look washed out. Prefer an SDR master.",
        )
    if (
        not settings.IS_IC_INSTANCE
        and master.master_released_at
        and not _verified_on_a_booth(master)
    ):
        add(
            ERROR,
            "master_lost",
            "Production released this master and no booth reports a verified copy; re-upload it.",
        )
    return problems


def item_problems(item):
    problems = []

    def add(severity, code, message):
        problems.append(Problem(severity, code, message))

    content = item.content
    if content.is_url_only() or not content.resource_id:
        add(
            ERROR,
            "needs_network",
            "URL/YouTube content needs the network, so it can't be shown in the booth.",
        )
        return problems
    if not (content.playlist_id and content.playlist.is_ic):
        add(
            ERROR,
            "not_ic_playlist",
            "The content isn't in an International Cinema playlist.",
        )

    master = item.projection_file()
    if master is None:
        add(ERROR, "no_master", "No projection master has been uploaded for this film.")
    else:
        problems.extend(master_problems(master))
        if not settings.IS_IC_INSTANCE:
            job = (
                master.media_jobs.filter(kind="transcode")
                .order_by("-created_at")
                .first()
            )
            derived_exists = master.derivatives.exists()
            if job is not None and job.is_pending:
                add(
                    ERROR,
                    "transcode_pending",
                    "The SD editing file is still being made from the master.",
                )
            elif (
                job is not None
                and job.status == job.Status.FAILED
                and not derived_exists
            ):
                add(
                    ERROR,
                    "transcode_failed",
                    f"Making the SD editing file failed: {job.error}",
                )
        uses_derivative = bool(content.resource_file_id) and (
            content.resource_file.derived_from_id == master.pk
        )
        if not uses_derivative and not item.timing_verified:
            add(
                ERROR,
                "timing_unverified",
                "The annotations were made on a file that wasn't transcoded from this "
                "master. Switch the content to the SD file made from the master, or check "
                "the timing and mark it verified.",
            )

    if item.subtitles == item.Subtitles.VTT and not content.default_subtitle_track_id:
        add(
            ERROR,
            "no_vtt",
            "Subtitles are set to VTT, but the content has no default subtitle track.",
        )
    if (
        item.subtitles == item.Subtitles.BURNED_IN
        and master is not None
        and not master.burned_in_subtitles_language_id
    ):
        add(
            WARNING,
            "burned_in_unknown",
            "Subtitles are set to burned-in, but the master doesn't record a burned-in language.",
        )
    # Loops are announcements and slates, which rarely need editing.
    if not content.annotation_set_id and item.mode != item.Mode.LOOP:
        add(
            WARNING, "no_annotations", "No annotation set is selected for this content."
        )
    return problems


def program_problems(program):
    items = list(program.ordered_items())
    if not items:
        return [Problem(ERROR, "empty", "The program has nothing in it yet.")]
    problems = []
    for item in items:
        problems.extend(item_problems(item))
    return problems


def rollup(problems):
    if any(problem.is_error for problem in problems):
        return ERROR
    if problems:
        return WARNING
    return "ready"
