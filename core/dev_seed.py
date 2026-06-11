from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from django.db import transaction

from .dev_features import DEMO_ADMIN_PASSWORD
from .dev_features import DEMO_ADMIN_USERNAME
from .factories import AnnotationSetFactory
from .factories import BlankAnnotationFactory
from .factories import ClipFactory
from .factories import CollectionFactory
from .factories import CollectionUserAccessFactory
from .factories import CommentAnnotationFactory
from .factories import ContentFactory
from .factories import CourseFactory
from .factories import LanguageFactory
from .factories import MuteAnnotationFactory
from .factories import ResourceAccessFactory
from .factories import ResourceFactory
from .factories import ResourceFileKeyFactory
from .factories import SubtitleFactory
from .factories import TrackFactory
from .factories import UserCourseFactory
from .factories import UserFactory
from .models import Collection
from .models import CollectionRole
from .models import Content
from .models import Course
from .models import Resource
from .models import ResourceFile
from .models import User

DEMO_MEDIA_DIR = Path(settings.BASE_DIR) / "demo_media"
DEMO_YEARTERM = "20261"
DEMO_ADMIN_USERNAMES = {
    DEMO_ADMIN_USERNAME,
    "profada",
    "profben",
    "caseyta",
    "labdemo",
    "studali",
    "studbob",
    "studivy",
}
DEMO_RESOURCE_NAMES = {"Birds", "Grid", "Grid Overlay"}
DEMO_COURSES = [
    ("BIO", "205", "001"),
    ("FILM", "330", "001"),
]
DEMO_SUBTITLE_VTT = """WEBVTT

00:00:00.000 --> 00:00:02.500
Birds gather near the shoreline.

00:00:02.500 --> 00:00:05.000
The sample media stays connected to a real mp4 file.
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
    english = LanguageFactory(language="English", lang_tag="en")
    spanish = LanguageFactory(language="Spanish", lang_tag="es")

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
        username="profada",
        netid="profada",
        first_name="Ada",
        last_name="Professor",
        email="profada@example.test",
        password="profada",
    )
    professor_ben = UserFactory(
        instructor=True,
        username="profben",
        netid="profben",
        first_name="Ben",
        last_name="Professor",
        email="profben@example.test",
        password="profben",
    )
    teaching_assistant = UserFactory(
        lab_assistant=True,
        username="caseyta",
        netid="caseyta",
        first_name="Casey",
        last_name="TA",
        email="caseyta@example.test",
        password="caseyta",
    )
    lab_assistant = UserFactory(
        lab_assistant=True,
        username="labdemo",
        netid="labdemo",
        first_name="Jordan",
        last_name="Lab",
        email="labdemo@example.test",
        password="labdemo",
    )
    student_alice = UserFactory(
        student=True,
        username="studali",
        netid="studali",
        first_name="Alice",
        last_name="Student",
        email="studali@example.test",
        password="studali",
    )
    student_bob = UserFactory(
        student=True,
        username="studbob",
        netid="studbob",
        first_name="Bob",
        last_name="Student",
        email="studbob@example.test",
        password="studbob",
    )
    student_ivy = UserFactory(
        student=True,
        username="studivy",
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
    )
    film_course = CourseFactory(
        dept="FILM",
        catalog_number="330",
        section_number="001",
    )

    for user, course in [
        (student_alice, biology_course),
        (student_bob, biology_course),
        (student_bob, film_course),
        (student_ivy, film_course),
    ]:
        UserCourseFactory(user=user, course=course, yearterm=DEMO_YEARTERM)

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

    ada_collection = CollectionFactory(
        owner=professor_ada,
        name="Professor Ada / Birds of a Feather",
        published=True,
        courses=[biology_course],
    )
    ada_drafts = CollectionFactory(
        owner=professor_ada,
        name="Professor Ada / Draft Lesson Shelf",
        published=False,
    )
    admin_collection = CollectionFactory(
        owner=admin,
        name="Local Admin / Demo Review Shelf",
        published=True,
    )
    admin_drafts = CollectionFactory(
        owner=admin,
        name="Local Admin / Draft Sandbox",
        published=False,
    )
    ben_collection = CollectionFactory(
        owner=professor_ben,
        name="Professor Ben / Visual Pattern Lab",
        published=True,
        courses=[film_course],
    )

    for collection, user, role in [
        (ada_collection, professor_ada, CollectionRole.INSTRUCTOR),
        (ada_collection, teaching_assistant, CollectionRole.TA),
        (ada_collection, student_alice, CollectionRole.STUDENT),
        (ada_collection, student_bob, CollectionRole.STUDENT),
        (ada_drafts, professor_ada, CollectionRole.INSTRUCTOR),
        (admin_collection, admin, CollectionRole.INSTRUCTOR),
        (admin_drafts, admin, CollectionRole.INSTRUCTOR),
        (ben_collection, professor_ben, CollectionRole.INSTRUCTOR),
        (ben_collection, teaching_assistant, CollectionRole.TA),
        (ben_collection, student_bob, CollectionRole.STUDENT),
        (ben_collection, student_ivy, CollectionRole.STUDENT),
    ]:
        CollectionUserAccessFactory(
            collection=collection,
            user=user,
            collection_role=role,
        )

    birds_clip = ClipFactory(
        resource=birds_resource,
        owner=professor_ada,
        name="Birds Intro Clip",
        start_time=2.5,
        end_time=18.0,
        description="Opening segment for the birds lesson.",
    )
    grid_clip = ClipFactory(
        resource=grid_resource,
        owner=professor_ben,
        name="Grid Demonstration Clip",
        start_time=1.0,
        end_time=9.5,
        description="Grid segment used in class exercises.",
    )

    birds_annotation_set = AnnotationSetFactory(
        name="Professor Ada Birds Annotations",
        resource=birds_resource,
        owner=professor_ada,
        editors=[teaching_assistant, admin],
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
    MuteAnnotationFactory(
        track=birds_track,
        name="Mute Interview Section",
        start_time=6.0,
        end_time=11.5,
        description="Mute the narration segment used for discussion prompts.",
    )
    BlankAnnotationFactory(
        track=birds_track,
        name="Blur Caption Cue",
        start_time=12.0,
        end_time=14.5,
        description="Blur an embedded lower-third before class discussion.",
        type="#",
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
        collection=ada_collection,
        resource_file=birds_file,
        annotation_set=birds_annotation_set,
        title="Birds Overview",
        description="Published lesson content for Professor Ada's students.",
        published=True,
        clips=[birds_clip],
    )
    ContentFactory(
        collection=ada_drafts,
        resource_file=birds_file,
        title="Birds Draft Discussion",
        description="Unpublished draft content for in-progress lesson work.",
        published=False,
    )
    ContentFactory(
        collection=admin_collection,
        resource_file=grid_file,
        annotation_set=grid_annotation_set,
        title="Admin Review Warmup",
        description="Published demo content for local admin review.",
        published=True,
    )
    ContentFactory(
        collection=admin_drafts,
        resource_file=overlay_file,
        title="Admin Draft Overlay Notes",
        description="Unpublished demo content for local admin testing.",
        published=False,
    )
    ContentFactory(
        collection=ben_collection,
        resource_file=grid_file,
        annotation_set=grid_annotation_set,
        title="Pattern Analysis Warmup",
        description="Published grid-based lesson content for Professor Ben.",
        published=True,
        clips=[grid_clip],
    )
    ContentFactory(
        collection=ben_collection,
        resource_file=overlay_file,
        title="Overlay Composition Exercise",
        description="Published content demonstrating the transparent-border asset.",
        published=True,
    )

    SubtitleFactory(
        resource=birds_resource,
        owner=professor_ada,
        language=english,
        name="English Demo Captions",
        subtitles_file=ContentFile(
            DEMO_SUBTITLE_VTT.encode("utf-8"), name="birds-demo.vtt"
        ),
        is_original=True,
    )
    ResourceFileKeyFactory(user=admin, resource_file=birds_file)

    return {
        "users": User.objects.filter(username__in=DEMO_ADMIN_USERNAMES).count(),
        "resources": Resource.objects.filter(name__in=DEMO_RESOURCE_NAMES).count(),
        "collections": Collection.objects.filter(
            owner__username__in=DEMO_ADMIN_USERNAMES
        ).count(),
        "contents": Content.objects.filter(
            collection__owner__username__in=DEMO_ADMIN_USERNAMES
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
