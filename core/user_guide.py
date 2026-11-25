"""Page list for the user guide at /user-guide/.

The order of PAGES is the reading order: it drives the sidebar, the prev/next links, and
which slugs exist. A page's parent is the slug with its last segment removed, and every
top-level section hangs off the introduction ("").
"""

from dataclasses import dataclass

from django.urls import reverse

TOOLS_SLUG = "edit-video/tools"


@dataclass(frozen=True)
class Page:
    slug: str
    title: str
    summary: str = ""
    heading: str = ""
    needs_review: bool = False

    @property
    def parent_slug(self):
        if not self.slug:
            return None
        return self.slug.rpartition("/")[0]

    @property
    def url(self):
        if not self.slug:
            return reverse("user_guide")
        return reverse("user_guide", args=[self.slug])

    @property
    def template(self):
        return f"core/user_guide/pages/{self.slug or 'index'}.html"

    @property
    def flagged(self):
        """Whether to show the needs-review dot. A section is flagged when anything inside
        it awaits sign-off; the introduction contains everything, so only its own copy counts.
        """
        if self.needs_review:
            return True
        return bool(self.slug) and any(
            child.flagged for child in children_of(self.slug)
        )

    @property
    def tool(self):
        """Annotation type a tool page documents, so it can wear the editor's color."""
        if self.parent_slug == TOOLS_SLUG:
            return self.slug.rpartition("/")[2]
        return ""


PAGES = [
    Page("", "Introduction", heading="Y Video User Guide", needs_review=True),
    Page(
        "getting-started",
        "Getting started",
        "Set up your first playlist and share edited videos with your students.",
        needs_review=True,
    ),
    Page(
        "playlists",
        "Manage playlists",
        "Build playlists, edit their details, publish them, and add collaborators.",
        needs_review=True,
    ),
    Page(
        "playlists/set-up",
        "Set up a playlist",
        "Create a new playlist, name it, and choose its thumbnail.",
        needs_review=True,
    ),
    Page(
        "playlists/details",
        "Edit playlist details",
        "Change a playlist’s title, description, and thumbnail, or duplicate it.",
        needs_review=True,
    ),
    Page(
        "playlists/video-details",
        "Edit video details",
        "Change a video’s availability, languages, and permission dates.",
        needs_review=True,
    ),
    Page(
        "playlists/videos",
        "Add videos",
        "Add a video to a playlist from Y Video, YouTube, or a new request.",
        needs_review=True,
    ),
    Page(
        "playlists/publish",
        "Publish a playlist",
        "Make a playlist visible to course sections and individuals.",
        needs_review=True,
    ),
    Page(
        "playlists/collaborators",
        "Add collaborators",
        "Invite teaching assistants or other faculty to edit a playlist.",
        needs_review=True,
    ),
    Page(
        "edit-video",
        "Edit your video",
        "Use the Overlay Editor and its seven tools to customize a video.",
    ),
    Page(
        "edit-video/create-overlay",
        "Create a new overlay",
        "Start a new set of edits on a video, and rename it later.",
        needs_review=True,
    ),
    Page(
        "edit-video/editor-tour",
        "Navigating the Overlay Editor",
        "The five panels of the Overlay Editor and what each one does.",
    ),
    Page(
        "edit-video/timeline",
        "Work with the timeline",
        "Organize tracks, move and retime tool items, and navigate long videos.",
    ),
    Page(
        "edit-video/add-delete",
        "Add and delete tool items",
        "Create a tool item, find it again, and remove it.",
    ),
    Page(
        "edit-video/adjust",
        "Adjust a tool item",
        "Rename, retime, describe, undo, and delete using the Properties Panel.",
    ),
    Page(
        TOOLS_SLUG,
        "Use each tool",
        "What each of the seven tools does, and the properties unique to it.",
    ),
    Page(f"{TOOLS_SLUG}/skip", "Skip", "Jump over a section of the video."),
    Page(f"{TOOLS_SLUG}/mute", "Mute", "Silence audio while keeping the picture."),
    Page(
        f"{TOOLS_SLUG}/blank", "Blank", "Replace the picture while keeping the audio."
    ),
    Page(f"{TOOLS_SLUG}/pause", "Pause", "Stop the video on a single frame."),
    Page(
        f"{TOOLS_SLUG}/blur",
        "Blur",
        "Blur a rectangular area, and animate it to follow a subject.",
    ),
    Page(
        f"{TOOLS_SLUG}/comment",
        "Comment",
        "Add custom text over the video.",
        needs_review=True,
    ),
    Page(f"{TOOLS_SLUG}/clip", "Clip", "Show only selected portions of the video."),
    Page(
        "edit-video/subtitles",
        "Add subtitles",
        "Choose or create a subtitle track, then edit its timing and text.",
        needs_review=True,
    ),
    Page(
        "edit-video/preview",
        "Preview your edits",
        "Play back your overlay, jump between clips, and read the transcript.",
    ),
    Page(
        "overlays",
        "Manage overlays",
        "Import, export, change, retire, move, copy, delete, and archive overlays.",
    ),
    Page(
        "overlays/import-export",
        "Import, export, change, or retire an overlay",
        "Bring in an exported overlay, or use the Overlay Settings menu in the editor.",
        needs_review=True,
    ),
    Page(
        "overlays/move-copy-delete",
        "Move, copy, delete, or archive an overlay",
        "Tidy up overlays, videos, and playlists you no longer need.",
        needs_review=True,
    ),
    Page(
        "glossary",
        "Glossary of terms",
        "Definitions of the terms used throughout Y Video.",
        needs_review=True,
    ),
]

PAGES_BY_SLUG = {page.slug: page for page in PAGES}


def children_of(slug):
    return [page for page in PAGES if page.parent_slug == slug]


def ancestors_of(page):
    """Section pages above this one, outermost first, not counting the introduction."""
    chain = []
    slug = page.parent_slug
    while slug:
        chain.insert(0, PAGES_BY_SLUG[slug])
        slug = PAGES_BY_SLUG[slug].parent_slug
    return chain


def nav_tree(current):
    def node(page):
        return {
            "page": page,
            "current": page == current,
            "open": page == current or current.slug.startswith(page.slug + "/"),
            "children": [node(child) for child in children_of(page.slug)]
            if page.slug
            else [],
        }

    root = PAGES_BY_SLUG[""]
    return [node(root)] + [node(page) for page in children_of("")]


def neighbors(page):
    index = PAGES.index(page)
    previous_page = PAGES[index - 1] if index > 0 else None
    next_page = PAGES[index + 1] if index + 1 < len(PAGES) else None
    return previous_page, next_page
