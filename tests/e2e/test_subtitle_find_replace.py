"""Browser coverage for subtitle whole-word find/replace and saved cues."""

from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import expect
import pytest

from core.factories import SubtitleFactory
from core.subtitle_uploads import generate_vtt_cues_from_file_path

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]

CUE_TEXTS = [
    "cat cat, (cat) cat-cat cats catalog bobcat Cat CAT",
    "café café, (café) cafés décaféiné Café café_1 café2 café\u0301",
    "cat! café? a.b (a.b) acb a+b [word]",
    "Unchanged subtitle.",
]
CUE_TIMES = [(0.125, 1.875), (2.125, 3.875), (4.125, 5.875), (6.125, 7.875)]


@pytest.fixture
def subtitle_track(demo_content):
    blocks = [
        f"00:00:0{index * 2}.125 --> 00:00:0{index * 2 + 1}.875\n{text}"
        for index, text in enumerate(CUE_TEXTS)
    ]
    return SubtitleFactory(
        resource=demo_content.get_resource(),
        owner=demo_content.playlist.owner,
        subtitles_file=SimpleUploadedFile(
            "find-replace.vtt",
            ("WEBVTT\n\n" + "\n\n".join(blocks) + "\n").encode("utf-8"),
            content_type="text/vtt",
        ),
    )


def _select_track(page, track):
    page.locator("#annotation-panel-switch").click()
    page.select_option("#subtitles-track-selector", str(track.pk))
    expect(page.locator(".editor-subtitle-cue-content")).to_have_count(len(CUE_TEXTS))
    page.locator("#cue-far-header").click()
    expect(page.locator("#cue-far-content")).to_be_visible()


def _assert_visible_cues(page, texts):
    cues = page.locator(".editor-subtitle-cue-content")
    expect(cues).to_have_count(len(texts))
    for index, text in enumerate(texts):
        expect(cues.nth(index)).to_have_value(text)


def _stored_cues(track):
    track.refresh_from_db()
    cues = generate_vtt_cues_from_file_path(track.subtitles_file.path)
    return [(cue.payload, cue.start_time, cue.end_time) for cue in cues]


def _expected_cues(texts):
    return [
        (text, start, end) for text, (start, end) in zip(texts, CUE_TIMES, strict=True)
    ]


def test_find_counts_whole_words_and_updates_replace_availability(
    page, open_editor, subtitle_track
):
    open_editor()
    _select_track(page, subtitle_track)
    find = page.locator("#cue-find-input")
    count = page.locator("#cue-find-match-count")
    replace = page.get_by_role("button", name="Replace all", exact=True)
    expect(replace).to_be_disabled()

    for text, expected in [
        ("cat", "6 matches"),
        ("café", "4 matches"),
        ("Cat", "1 match"),
        ("CAT", "1 match"),
        ("a.b", "2 matches"),
        ("a+b", "1 match"),
        ("[word]", "1 match"),
        ("missing", "0 matches"),
    ]:
        find.fill(text)
        expect(count).to_have_text(expected)
        if expected == "0 matches":
            expect(replace).to_be_disabled()
        else:
            expect(replace).to_be_enabled()

    find.fill("")
    expect(count).to_have_text("")
    expect(replace).to_be_disabled()
    assert _stored_cues(subtitle_track) == _expected_cues(CUE_TEXTS)


@pytest.mark.parametrize(
    ("find_text", "replacement", "match_count", "expected"),
    [
        (
            "cat",
            "dog",
            6,
            [
                "dog dog, (dog) dog-dog cats catalog bobcat Cat CAT",
                CUE_TEXTS[1],
                "dog! café? a.b (a.b) acb a+b [word]",
                CUE_TEXTS[3],
            ],
        ),
        (
            "café",
            "tea",
            4,
            [
                CUE_TEXTS[0],
                "tea tea, (tea) cafés décaféiné Café café_1 café2 café\u0301",
                "cat! tea? a.b (a.b) acb a+b [word]",
                CUE_TEXTS[3],
            ],
        ),
        (
            "a.b",
            "$&",
            2,
            [
                CUE_TEXTS[0],
                CUE_TEXTS[1],
                "cat! café? $& ($&) acb a+b [word]",
                CUE_TEXTS[3],
            ],
        ),
    ],
    ids=[
        "whole-word-punctuation-case",
        "unicode-boundaries",
        "literal-regex-and-dollar",
    ],
)
def test_replace_saves_cue_text_and_preserves_timings(
    page, open_editor, subtitle_track, find_text, replacement, match_count, expected
):
    open_editor()
    _select_track(page, subtitle_track)
    _assert_visible_cues(page, CUE_TEXTS)
    assert _stored_cues(subtitle_track) == _expected_cues(CUE_TEXTS)
    page.locator("#cue-find-input").fill(find_text)
    expect(page.locator("#cue-find-match-count")).to_have_text(f"{match_count} matches")
    page.locator("#cue-replace-input").fill(replacement)

    endpoint = f"/subtitles/{subtitle_track.pk}/update-cues/"
    with page.expect_response(
        lambda response: (
            response.url.endswith(endpoint) and response.request.method == "POST"
        )
    ) as saved:
        page.get_by_role("button", name="Replace all", exact=True).click()
    response = saved.value
    assert response.status == 200
    body = response.request.post_data_json
    assert body["seconds_nudge"] == 0
    assert body["nudge_excluded_cues"] == []
    assert [cue["type"] for cue in body["cues"]] == ["CUE"] * len(CUE_TEXTS)
    assert [cue["payload"] for cue in body["cues"]] == expected
    _assert_visible_cues(page, expected)
    expect(page.locator("#cue-find-input")).to_have_value("")
    expect(page.locator("#cue-find-match-count")).to_have_text("")
    assert _stored_cues(subtitle_track) == _expected_cues(expected)

    open_editor()
    _select_track(page, subtitle_track)
    _assert_visible_cues(page, expected)
    assert _stored_cues(subtitle_track) == _expected_cues(expected)


@pytest.mark.parametrize("replacement", ["", "   "], ids=["empty", "whitespace-only"])
def test_empty_replacement_does_not_save_or_change_cues(
    page, open_editor, subtitle_track, replacement
):
    open_editor()
    _select_track(page, subtitle_track)
    original_file = subtitle_track.subtitles_file.name
    original_updated_at = subtitle_track.updated_at
    endpoint = f"/subtitles/{subtitle_track.pk}/update-cues/"
    saves = []

    def record_save(request):
        if request.method == "POST" and request.url.endswith(endpoint):
            saves.append(request)

    page.on("request", record_save)
    try:
        page.locator("#cue-find-input").fill("cat")
        expect(page.locator("#cue-find-match-count")).to_have_text("6 matches")
        page.locator("#cue-replace-input").fill(replacement)
        page.get_by_role("button", name="Replace all", exact=True).click()
        _assert_visible_cues(page, CUE_TEXTS)
        expect(page.locator("#cue-find-input")).to_have_value("cat")
        expect(page.locator("#cue-find-match-count")).to_have_text("6 matches")

        open_editor()
        _select_track(page, subtitle_track)
        _assert_visible_cues(page, CUE_TEXTS)
        assert _stored_cues(subtitle_track) == _expected_cues(CUE_TEXTS)
        assert subtitle_track.subtitles_file.name == original_file
        assert subtitle_track.updated_at == original_updated_at
        assert saves == []
    finally:
        page.remove_listener("request", record_save)


def test_replace_all_disables_itself_so_a_double_click_replaces_once(
    page, open_editor, subtitle_track
):
    open_editor()
    _select_track(page, subtitle_track)
    replace = page.get_by_role("button", name="Replace all", exact=True)
    page.locator("#cue-find-input").fill("cat")
    expect(page.locator("#cue-find-match-count")).to_have_text("6 matches")
    page.locator("#cue-replace-input").fill("dog")

    replace.dblclick()
    expect(replace).to_be_disabled()
    expected = [
        "dog dog, (dog) dog-dog cats catalog bobcat Cat CAT",
        CUE_TEXTS[1],
        "dog! café? a.b (a.b) acb a+b [word]",
        CUE_TEXTS[3],
    ]
    _assert_visible_cues(page, expected)
    expect(page.locator("#subtitle-save-status")).to_contain_text("Changes saved")
    assert _stored_cues(subtitle_track) == _expected_cues(expected)


@pytest.mark.parametrize("find_text", ["", "   "], ids=["empty", "whitespace-only"])
def test_replace_all_never_runs_with_a_blank_search(
    page, open_editor, subtitle_track, find_text
):
    """The handler itself refuses a blank search, even if the button were enabled."""
    open_editor()
    _select_track(page, subtitle_track)
    endpoint = f"/subtitles/{subtitle_track.pk}/update-cues/"
    saves = []

    def record_save(request):
        if request.method == "POST" and request.url.endswith(endpoint):
            saves.append(request)

    page.on("request", record_save)
    try:
        page.locator("#cue-find-input").fill(find_text)
        page.locator("#cue-replace-input").fill("dog")
        page.locator("#execute-far-button").evaluate(
            "button => { button.disabled = false; button.click(); }"
        )
        expect(page.locator("#execute-far-button")).to_be_disabled()
        page.wait_for_timeout(500)
        _assert_visible_cues(page, CUE_TEXTS)
        assert _stored_cues(subtitle_track) == _expected_cues(CUE_TEXTS)
        assert saves == []
    finally:
        page.remove_listener("request", record_save)


def test_editing_the_search_disables_replace_until_the_count_catches_up(
    page, open_editor, subtitle_track
):
    open_editor()
    _select_track(page, subtitle_track)
    find = page.locator("#cue-find-input")
    replace = page.get_by_role("button", name="Replace all", exact=True)
    find.fill("cat")
    expect(page.locator("#cue-find-match-count")).to_have_text("6 matches")
    expect(replace).to_be_enabled()

    # Well inside the 500 ms debounce, so only the immediate disable can satisfy this.
    find.fill("")
    expect(replace).to_be_disabled(timeout=250)


def test_match_count_includes_cue_edits_that_were_not_rerendered(
    page, open_editor, subtitle_track
):
    open_editor()
    _select_track(page, subtitle_track)
    # A failed save leaves the edit in the textarea without re-rendering it from the server.
    page.route(
        f"**/subtitles/{subtitle_track.pk}/update-cues/",
        lambda route: route.fulfill(status=500),
    )
    last_cue = page.locator(".editor-subtitle-cue-content").nth(3)
    last_cue.fill("cat subtitle.")
    last_cue.press("Enter")
    expect(page.locator("#subtitle-save-status")).to_contain_text("Save failed")

    page.locator("#cue-find-input").fill("cat")
    expect(page.locator("#cue-find-match-count")).to_have_text("7 matches")


def test_save_status_baseline_follows_the_replaced_cues(
    page, open_editor, subtitle_track
):
    open_editor()
    _select_track(page, subtitle_track)
    status_text = page.locator("#subtitle-save-status .save-status-text")
    page.locator("#cue-find-input").fill("cat")
    expect(page.locator("#cue-find-match-count")).to_have_text("6 matches")
    page.locator("#cue-replace-input").fill("dog")
    page.get_by_role("button", name="Replace all", exact=True).click()
    replaced = "dog dog, (dog) dog-dog cats catalog bobcat Cat CAT"
    expect(page.locator(".editor-subtitle-cue-content").first).to_have_value(replaced)
    expect(status_text).to_have_text("Changes saved")

    first_cue = page.locator(".editor-subtitle-cue-content").first
    first_cue.fill(replaced + "!")
    expect(status_text).to_have_text("Waiting to autosave...")
    # Typing back to the saved (replaced) text leaves nothing to save.
    first_cue.fill(replaced)
    expect(status_text).not_to_have_text("Waiting to autosave...")


def test_search_text_is_trimmed(page, open_editor, subtitle_track):
    open_editor()
    _select_track(page, subtitle_track)
    page.locator("#cue-find-input").fill("  cat ")
    expect(page.locator("#cue-find-match-count")).to_have_text("6 matches")
    page.locator("#cue-replace-input").fill("dog")
    page.get_by_role("button", name="Replace all", exact=True).click()
    expected = [
        "dog dog, (dog) dog-dog cats catalog bobcat Cat CAT",
        CUE_TEXTS[1],
        "dog! café? a.b (a.b) acb a+b [word]",
        CUE_TEXTS[3],
    ]
    _assert_visible_cues(page, expected)
    assert _stored_cues(subtitle_track) == _expected_cues(expected)


def test_composed_search_matches_decomposed_cue_text(page, open_editor, demo_content):
    decomposed = "café au lait"
    untouched = "Unchanged cafés."
    track = SubtitleFactory(
        resource=demo_content.get_resource(),
        owner=demo_content.playlist.owner,
        subtitles_file=SimpleUploadedFile(
            "nfd.vtt",
            (
                "WEBVTT\n\n"
                f"00:00:00.125 --> 00:00:01.875\n{decomposed}\n\n"
                f"00:00:02.125 --> 00:00:03.875\n{untouched}\n"
            ).encode(),
            content_type="text/vtt",
        ),
    )
    open_editor()
    page.locator("#annotation-panel-switch").click()
    page.select_option("#subtitles-track-selector", str(track.pk))
    expect(page.locator(".editor-subtitle-cue-content")).to_have_count(2)
    page.locator("#cue-far-header").click()

    page.locator("#cue-find-input").fill("café")  # composed é
    expect(page.locator("#cue-find-match-count")).to_have_text("1 match")
    page.locator("#cue-replace-input").fill("tea")
    page.get_by_role("button", name="Replace all", exact=True).click()

    cues = page.locator(".editor-subtitle-cue-content")
    expect(cues.nth(0)).to_have_value("tea au lait")
    # A cue without a match is not rewritten, so it keeps its decomposed form.
    expect(cues.nth(1)).to_have_value(untouched)
    track.refresh_from_db()
    stored = [
        cue.payload
        for cue in generate_vtt_cues_from_file_path(track.subtitles_file.path)
    ]
    assert stored == ["tea au lait", untouched]


def test_failed_replace_keeps_the_search_so_it_can_be_retried(
    page, open_editor, subtitle_track
):
    open_editor()
    _select_track(page, subtitle_track)
    page.route(
        f"**/subtitles/{subtitle_track.pk}/update-cues/",
        lambda route: route.fulfill(status=500),
    )
    page.locator("#cue-find-input").fill("cat")
    expect(page.locator("#cue-find-match-count")).to_have_text("6 matches")
    page.locator("#cue-replace-input").fill("dog")
    replace = page.get_by_role("button", name="Replace all", exact=True)
    replace.click()

    expect(page.locator("#subtitle-save-status")).to_contain_text("Save failed")
    expect(page.locator("#cue-find-input")).to_have_value("cat")
    expect(page.locator("#cue-find-match-count")).to_have_text("6 matches")
    expect(replace).to_be_enabled()
    _assert_visible_cues(page, CUE_TEXTS)
    assert _stored_cues(subtitle_track) == _expected_cues(CUE_TEXTS)
