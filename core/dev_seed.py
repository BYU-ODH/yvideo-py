from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from django.db import transaction

from .dev_features import DEMO_ADMIN_NETID
from .dev_features import DEMO_ADMIN_PASSWORD
from .factories import AnnotationSetFactory
from .factories import BlankAnnotationFactory
from .factories import ClipFactory
from .factories import CollectionFactory
from .factories import CollectionUserAccessFactory
from .factories import ContentFactory
from .factories import CourseFactory
from .factories import LanguageFactory
from .factories import MuteAnnotationFactory
from .factories import ResourceAccessFactory
from .factories import ResourceFactory
from .factories import SubtitleFactory
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
DEMO_USER_NETIDS = {
    DEMO_ADMIN_NETID,
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
    User.objects.filter(netid__in=DEMO_USER_NETIDS).delete()
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
        netid=DEMO_ADMIN_NETID,
        first_name="Local",
        last_name="Admin",
        email="devadmin@example.test",
        password=DEMO_ADMIN_PASSWORD,
    )
    professor_ada = UserFactory(
        instructor=True,
        netid="profada",
        first_name="Ada",
        last_name="Professor",
        email="profada@example.test",
        password="profada",
    )
    professor_ben = UserFactory(
        instructor=True,
        netid="profben",
        first_name="Ben",
        last_name="Professor",
        email="profben@example.test",
        password="profben",
    )
    teaching_assistant = UserFactory(
        lab_assistant=True,
        netid="caseyta",
        first_name="Casey",
        last_name="TA",
        email="caseyta@example.test",
        password="caseyta",
    )
    lab_assistant = UserFactory(
        lab_assistant=True,
        netid="labdemo",
        first_name="Jordan",
        last_name="Lab",
        email="labdemo@example.test",
        password="labdemo",
    )
    student_alice = UserFactory(
        student=True,
        netid="studali",
        first_name="Alice",
        last_name="Student",
        email="studali@example.test",
        password="studali",
    )
    student_bob = UserFactory(
        student=True,
        netid="studbob",
        first_name="Bob",
        last_name="Student",
        email="studbob@example.test",
        password="studbob",
    )
    student_ivy = UserFactory(
        student=True,
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

    birds_resource = ResourceFactory(name="Birds", requester_netid=professor_ada.netid)
    grid_resource = ResourceFactory(name="Grid", requester_netid=professor_ben.netid)
    overlay_resource = ResourceFactory(
        name="Grid Overlay",
        requester_netid=professor_ben.netid,
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
        editors=[teaching_assistant],
    )
    MuteAnnotationFactory(
        owner=professor_ada,
        annotation_set=birds_annotation_set,
        name="Mute Interview Section",
        start_time=6.0,
        end_time=11.5,
        description="Mute the narration segment used for discussion prompts.",
    )
    BlankAnnotationFactory(
        owner=professor_ada,
        annotation_set=birds_annotation_set,
        name="Blur Caption Cue",
        start_time=12.0,
        end_time=14.5,
        description="Blur an embedded lower-third before class discussion.",
        type="#",
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

    return {
        "users": User.objects.filter(netid__in=DEMO_USER_NETIDS).count(),
        "resources": Resource.objects.filter(name__in=DEMO_RESOURCE_NAMES).count(),
        "collections": Collection.objects.filter(
            owner__netid__in=DEMO_USER_NETIDS
        ).count(),
        "contents": Content.objects.filter(
            collection__owner__netid__in=DEMO_USER_NETIDS
        ).count(),
        "seeded_admin_netid": DEMO_ADMIN_NETID,
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
