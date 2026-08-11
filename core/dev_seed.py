from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.files import File
from django.core.files.base import ContentFile
from django.db import transaction

from .dev_features import DEMO_ADMIN_PASSWORD
from .dev_features import DEMO_ADMIN_USERNAME
from .factories import AnnotationSetFactory
from .factories import BlankAnnotationFactory
from .factories import BlurAnnotationFactory
from .factories import BlurAnnotationPositionFactory
from .factories import ClipFactory
from .factories import CommentAnnotationFactory
from .factories import ContentFactory
from .factories import CourseFactory
from .factories import LanguageFactory
from .factories import MuteAnnotationFactory
from .factories import PlaylistFactory
from .factories import PlaylistUserAccessFactory
from .factories import ResourceAccessFactory
from .factories import ResourceFactory
from .factories import ResourceFileKeyFactory
from .factories import SubtitleFactory
from .factories import TrackFactory
from .factories import UserCourseFactory
from .factories import UserFactory
from .models import LAB_ASSISTANT_GROUP_NAME
from .models import Content
from .models import Course
from .models import Playlist
from .models import PlaylistRole
from .models import Resource
from .models import ResourceFile
from .models import User
from .utils import estimate_current_yearterm

DEMO_MEDIA_DIR = Path(settings.BASE_DIR) / "demo_media"
DEMO_ADMIN_USERNAMES = {
    DEMO_ADMIN_USERNAME,
    "111223333",
    "111224444",
    "111225555",
    "111226666",
    "111227777",
    "111228888",
    "111229999",
}
DEMO_RESOURCE_NAMES = {"Birds", "Grid", "Grid Overlay"}
DEMO_COURSES = [
    ("BIO", "205", "001"),
    ("FILM", "330", "001"),
]
DEMO_SUBTITLE_VTT_EN = """WEBVTT

00:00:00.000 --> 00:00:02.500
Birds gather near the shoreline.

00:00:02.500 --> 00:00:05.000
The sample media stays connected to a real mp4 file.
"""

DEMO_SUBTITLE_VTT_ES = """WEBVTT

00:00:00.000 --> 00:00:02.500
Las aves se reúnen cerca de la orilla.

00:00:02.500 --> 00:00:05.000
El medio de muestra permanece conectado a un archivo mp4 real.
"""

DEMO_SUBTITLE_VTT_GRID_EN = """WEBVTT

00:00:00.000 --> 00:00:02.500
A color grid pattern fills the screen.

00:00:02.500 --> 00:00:05.000
Each cell shifts hue during the demonstration.
"""

DEMO_SUBTITLE_VTT_GRID_ES = """WEBVTT

00:00:00.000 --> 00:00:02.500
Un patrón de cuadrícula de color llena la pantalla.

00:00:02.500 --> 00:00:05.000
Cada celda cambia de tono durante la demostración.
"""

DEMO_SUBTITLE_VTT_OVERLAY_EN = """WEBVTT

00:00:00.000 --> 00:00:02.500
A transparent-border overlay is layered on top of the grid.

00:00:02.500 --> 00:00:05.000
The overlay demonstrates compositing two assets together.
"""

DEMO_SUBTITLE_VTT_OVERLAY_ES = """WEBVTT

00:00:00.000 --> 00:00:02.500
Una superposición de borde transparente se coloca sobre la cuadrícula.

00:00:02.500 --> 00:00:05.000
La superposición demuestra cómo combinar dos recursos.
"""


def seed_demo_data():
    with transaction.atomic():
        purge_demo_data()
        return create_demo_data()


def purge_demo_data():
    User.objects.filter(username__in=DEMO_ADMIN_USERNAMES).delete()
    Resource.objects.filter(name__in=DEMO_RESOURCE_NAMES).delete()

    for dept, catalog_number, section_number in DEMO_COURSES:
        Course.objects.filter(
            dept=dept,
            catalog_number=catalog_number,
            section_number=section_number,
        ).delete()


def create_demo_data():
    # compute once so every demo course and enrollment shares the same term
    demo_yearterm = estimate_current_yearterm()

    english = LanguageFactory(language="English", bcp47="en")
    spanish = LanguageFactory(language="Spanish", bcp47="es")

    admin = UserFactory(
        admin=True,
        username=DEMO_ADMIN_USERNAME,
        netid=DEMO_ADMIN_USERNAME,
        first_name="Local",
        last_name="Admin",
        email="devadmin@example.test",
        password=DEMO_ADMIN_PASSWORD,
    )
    professor_ada = UserFactory(
        instructor=True,
        username="111223333",
        netid="profada",
        first_name="Ada",
        last_name="Professor",
        email="profada@example.test",
        password="profada",
    )
    professor_ben = UserFactory(
        instructor=True,
        username="111224444",
        netid="profben",
        first_name="Ben",
        last_name="Professor",
        email="profben@example.test",
        password="profben",
    )
    teaching_assistant = UserFactory(
        student=True,
        username="111225555",
        netid="caseyta",
        first_name="Casey",
        last_name="TA",
        email="caseyta@example.test",
        password="caseyta",
    )
    lab_assistant = UserFactory(
        student=True,
        username="111226666",
        netid="labdemo",
        first_name="Jordan",
        last_name="Lab",
        email="labdemo@example.test",
        password="labdemo",
    )
    la_group = Group.objects.get(name=LAB_ASSISTANT_GROUP_NAME)
    lab_assistant.groups.add(la_group)

    student_alice = UserFactory(
        student=True,
        username="111227777",
        netid="studali",
        first_name="Alice",
        last_name="Student",
        email="studali@example.test",
        password="studali",
    )
    student_bob = UserFactory(
        student=True,
        username="111228888",
        netid="studbob",
        first_name="Bob",
        last_name="Student",
        email="studbob@example.test",
        password="studbob",
    )
    student_ivy = UserFactory(
        student=True,
        username="111229999",
        netid="studivy",
        first_name="Ivy",
        last_name="Student",
        email="studivy@example.test",
        password="studivy",
    )

    biology_course = CourseFactory(
        dept="BIO",
        catalog_number="205",
        section_number="001",
        yearterm=demo_yearterm,
    )
    film_course = CourseFactory(
        dept="FILM",
        catalog_number="330",
        section_number="001",
        yearterm=demo_yearterm,
    )

    for user, course in [
        (student_alice, biology_course),
        (student_bob, biology_course),
        (student_bob, film_course),
        (student_ivy, film_course),
    ]:
        UserCourseFactory(user=user, course=course, yearterm=demo_yearterm)

    birds_resource = ResourceFactory(
        name="Birds", requester_username=professor_ada.username
    )
    grid_resource = ResourceFactory(
        name="Grid", requester_username=professor_ben.username
    )
    overlay_resource = ResourceFactory(
        name="Grid Overlay",
        requester_username=professor_ben.username,
        copyrighted=True,
    )

    for user, resource in [
        (professor_ada, birds_resource),
        (teaching_assistant, birds_resource),
        (lab_assistant, birds_resource),
        (professor_ben, grid_resource),
        (teaching_assistant, grid_resource),
        (professor_ben, overlay_resource),
    ]:
        ResourceAccessFactory(user=user, resource=resource)

    birds_file = create_media_resource_file(
        resource=birds_resource,
        version="original_no_audio",
        fixture_filename="birds.mp4",
        audio_language=english,
    )
    grid_file = create_media_resource_file(
        resource=grid_resource,
        version="original",
        fixture_filename="color_grid.mp4",
        audio_language=english,
    )
    overlay_file = create_media_resource_file(
        resource=overlay_resource,
        version="transparent_border",
        fixture_filename="color_grid_trans_border.mp4",
        audio_language=english,
        burned_in_subtitles_language=spanish,
    )

    ada_playlist = PlaylistFactory(
        owner=professor_ada,
        name="Professor Ada / Birds of a Feather",
        published=True,
        courses=[biology_course],
    )
    ada_drafts = PlaylistFactory(
        owner=professor_ada,
        name="Professor Ada / Draft Lesson Shelf",
        published=False,
    )
    admin_playlist = PlaylistFactory(
        owner=admin,
        name="Local Admin / Demo Review Shelf",
        published=True,
    )
    admin_drafts = PlaylistFactory(
        owner=admin,
        name="Local Admin / Draft Sandbox",
        published=False,
    )
    ben_playlist = PlaylistFactory(
        owner=professor_ben,
        name="Professor Ben / Visual Pattern Lab",
        published=True,
        courses=[film_course],
    )

    for playlist, user, role in [
        (ada_playlist, professor_ada, PlaylistRole.INSTRUCTOR),
        (ada_playlist, teaching_assistant, PlaylistRole.TA),
        (ada_playlist, student_alice, PlaylistRole.STUDENT),
        (ada_playlist, student_bob, PlaylistRole.STUDENT),
        (ada_drafts, professor_ada, PlaylistRole.INSTRUCTOR),
        (admin_playlist, admin, PlaylistRole.INSTRUCTOR),
        (admin_drafts, admin, PlaylistRole.INSTRUCTOR),
        (ben_playlist, professor_ben, PlaylistRole.INSTRUCTOR),
        (ben_playlist, teaching_assistant, PlaylistRole.TA),
        (ben_playlist, student_bob, PlaylistRole.STUDENT),
        (ben_playlist, student_ivy, PlaylistRole.STUDENT),
    ]:
        PlaylistUserAccessFactory(
            playlist=playlist,
            user=user,
            playlist_role=role,
        )

    birds_annotation_set = AnnotationSetFactory(
        name="Professor Ada Birds Annotations",
        resource=birds_resource,
        owner=professor_ada,
    )
    birds_track = TrackFactory(
        annotation_set=birds_annotation_set,
        name="Track 1",
        stack_position=0,
    )
    grid_annotation_set = AnnotationSetFactory(
        name="Professor Ben Grid Annotations",
        resource=grid_resource,
        owner=professor_ben,
    )
    grid_track = TrackFactory(
        annotation_set=grid_annotation_set,
        name="Track 1",
        stack_position=0,
    )
    birds_clip = ClipFactory(
        track=birds_track,
        name="Birds Intro Clip",
        start_time=2.5,
        end_time=18.0,
        description="Opening segment for the birds lesson.",
    )
    grid_clip = ClipFactory(
        track=grid_track,
        name="Grid Demonstration Clip",
        start_time=1.0,
        end_time=9.5,
        description="Grid segment used in class exercises.",
    )
    MuteAnnotationFactory(
        track=birds_track,
        name="Mute Interview Section",
        start_time=6.0,
        end_time=11.5,
        description="Mute the narration segment used for discussion prompts.",
    )
    BlankAnnotationFactory(
        track=birds_track,
        name="birds blank",
        start_time=12.0,
        end_time=14.5,
        description="Blank the display",
        type="#",
    )
    # Two blurs covering both authoring shapes. Every position is deliberately asymmetric
    # (x != y, width != height) so an axis or dimension mix-up cannot cancel itself out, and
    # both stay clear of the 12.0-14.5 full-screen blank above, which would otherwise mask
    # them and make manual verification impossible.
    birds_watermark_blur = BlurAnnotationFactory(
        track=birds_track,
        name="Bird Watermark",
        start_time=1.0,
        end_time=5.0,
        description="Stationary blur covering a burned-in watermark.",
    )
    BlurAnnotationPositionFactory(
        blur_annotation=birds_watermark_blur,
        time=1.0,
        x=68.0,
        y=72.0,
        width=26.0,
        height=12.0,
    )
    # Overlaps the watermark from 3.0-5.0 on purpose: two blurs on screen at once is what
    # catches a renderer that keys its overlay divs by array index instead of annotation id.
    birds_flight_blur = BlurAnnotationFactory(
        track=birds_track,
        name="Bird Flight Path",
        start_time=3.0,
        end_time=11.0,
        description="Moving blur that follows a bird across the frame.",
    )
    for position_time, x, y, width, height in (
        (3.0, 12.5, 30.0, 22.0, 14.0),
        (7.0, 40.0, 22.0, 26.0, 17.0),
        (11.0, 66.5, 44.0, 18.0, 11.0),
    ):
        BlurAnnotationPositionFactory(
            blur_annotation=birds_flight_blur,
            time=position_time,
            x=x,
            y=y,
            width=width,
            height=height,
        )
    CommentAnnotationFactory(
        track=birds_track,
        name="Bird Notes 1",
        start_time=8.0,
        end_time=15.0,
        description="Prompt students to compare the birds' motion.",
        text="Notice how the group gathers before the turn.",
        top_left_x=42.0,
        top_left_y=28.0,
        bottom_right_x=20.0,
        bottom_right_y=10.0,
        font_size_in_rem=2.0,
        font_color="aaaaaa",
    )
    CommentAnnotationFactory(
        track=birds_track,
        name="Bird Notes 2",
        start_time=12.0,
        end_time=19.5,
        description="Call out the wing movement overlap on the same track.",
        text="Compare the spacing between the front and rear birds.",
        top_left_x=32.0,
        top_left_y=18.0,
        bottom_right_x=25.0,
        bottom_right_y=15.0,
        font_size_in_rem=1.5,
        font_color="bbbbbb",
    )
    CommentAnnotationFactory(
        track=birds_track,
        name="Bird Notes 3",
        start_time=16.0,
        end_time=22.5,
        description="Keep one more overlapping comment active later in the clip.",
        text="The final turn stays visible long enough to test overlap rendering.",
        top_left_x=37.0,
        top_left_y=23.0,
        bottom_right_x=22.0,
        bottom_right_y=18.0,
        font_size_in_rem=1.0,
        font_color="cccccc",
    )
    MuteAnnotationFactory(
        track=grid_track,
        name="Mute Tone Sweep",
        start_time=0.5,
        end_time=1.5,
        description="Mute the audio cue used during the pattern demo.",
    )
    BlankAnnotationFactory(
        track=grid_track,
        name="Black Out Grid Cell",
        start_time=2.0,
        end_time=3.0,
        description="Hide the highlighted square during discussion.",
        type="k",
    )
    CommentAnnotationFactory(
        track=grid_track,
        name="Grid Callout",
        start_time=4.0,
        end_time=9.0,
        description="Call out the grid region students should focus on.",
        text="Watch how the pattern shifts across the center cells.",
        top_left_x=50.0,
        top_left_y=50.0,
        bottom_right_x=25.0,
        bottom_right_y=20.0,
        font_size_in_rem=2.5,
        font_color="dddddd",
    )

    birds_content = ContentFactory(
        playlist=ada_playlist,
        resource_file=birds_file,
        annotation_set=birds_annotation_set,
        title="Birds Overview",
        description="Published lesson content for Professor Ada's students.",
        published=True,
    )
    ContentFactory(
        playlist=ada_drafts,
        resource_file=birds_file,
        title="Birds Draft Discussion",
        description="Unpublished draft content for in-progress lesson work.",
        published=False,
    )
    ContentFactory(
        playlist=admin_playlist,
        resource_file=grid_file,
        annotation_set=grid_annotation_set,
        title="Admin Review Warmup",
        description="Published demo content for local admin review.",
        published=True,
    )
    ContentFactory(
        playlist=admin_drafts,
        resource_file=overlay_file,
        title="Admin Draft Overlay Notes",
        description="Unpublished demo content for local admin testing.",
        published=False,
    )
    ContentFactory(
        playlist=ben_playlist,
        resource_file=grid_file,
        annotation_set=grid_annotation_set,
        title="Pattern Analysis Warmup",
        description="Published grid-based lesson content for Professor Ben.",
        published=True,
    )
    ContentFactory(
        playlist=ben_playlist,
        resource_file=overlay_file,
        title="Overlay Composition Exercise",
        description="Published content demonstrating the transparent-border asset.",
        published=True,
    )

    birds_en_subtitle = SubtitleFactory(
        resource=birds_resource,
        owner=professor_ada,
        language=english,
        name="English Captions",
        subtitles_file=ContentFile(
            DEMO_SUBTITLE_VTT_EN.encode("utf-8"), name="birds-en.vtt"
        ),
        is_original=True,
    )
    SubtitleFactory(
        resource=birds_resource,
        owner=professor_ada,
        language=spanish,
        name="Spanish Captions",
        subtitles_file=ContentFile(
            DEMO_SUBTITLE_VTT_ES.encode("utf-8"), name="birds-es.vtt"
        ),
        is_original=False,
    )
    SubtitleFactory(
        resource=grid_resource,
        owner=professor_ben,
        language=english,
        name="English Captions",
        subtitles_file=ContentFile(
            DEMO_SUBTITLE_VTT_GRID_EN.encode("utf-8"), name="grid-en.vtt"
        ),
        is_original=True,
    )
    SubtitleFactory(
        resource=grid_resource,
        owner=professor_ben,
        language=spanish,
        name="Spanish Captions",
        subtitles_file=ContentFile(
            DEMO_SUBTITLE_VTT_GRID_ES.encode("utf-8"), name="grid-es.vtt"
        ),
        is_original=False,
    )
    SubtitleFactory(
        resource=overlay_resource,
        owner=professor_ben,
        language=english,
        name="English Captions",
        subtitles_file=ContentFile(
            DEMO_SUBTITLE_VTT_OVERLAY_EN.encode("utf-8"), name="overlay-en.vtt"
        ),
        is_original=True,
    )
    SubtitleFactory(
        resource=overlay_resource,
        owner=professor_ben,
        language=spanish,
        name="Spanish Captions",
        subtitles_file=ContentFile(
            DEMO_SUBTITLE_VTT_OVERLAY_ES.encode("utf-8"), name="overlay-es.vtt"
        ),
        is_original=False,
    )

    birds_content.default_subtitle_track = birds_en_subtitle
    birds_content.save()

    ResourceFileKeyFactory(user=admin, resource_file=birds_file)

    return {
        "users": User.objects.filter(username__in=DEMO_ADMIN_USERNAMES).count(),
        "resources": Resource.objects.filter(name__in=DEMO_RESOURCE_NAMES).count(),
        "playlists": Playlist.objects.filter(
            owner__username__in=DEMO_ADMIN_USERNAMES
        ).count(),
        "contents": Content.objects.filter(
            playlist__owner__username__in=DEMO_ADMIN_USERNAMES
        ).count(),
        "seeded_admin_netid": DEMO_ADMIN_USERNAME,
        "seeded_admin_password": DEMO_ADMIN_PASSWORD,
        "sample_content_title": birds_content.title,
    }


def create_media_resource_file(
    *,
    resource,
    version,
    fixture_filename,
    audio_language=None,
    burned_in_subtitles_language=None,
):
    source_path = DEMO_MEDIA_DIR / fixture_filename
    resource_file = ResourceFile(
        resource=resource,
        version=version,
        full_video=True,
        audio_language=audio_language,
        burned_in_subtitles_language=burned_in_subtitles_language,
    )
    with source_path.open("rb") as fixture_handle:
        resource_file.file.save(source_path.name, File(fixture_handle), save=False)
    resource_file.save()
    return resource_file
