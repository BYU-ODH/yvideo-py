from pathlib import Path

from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
import factory

from .models import LAB_ASSISTANT_GROUP_NAME
from .models import AnnotationSet
from .models import BlankAnnotation
from .models import Clip
from .models import CommentAnnotation
from .models import Content
from .models import Course
from .models import Language
from .models import MuteAnnotation
from .models import Playlist
from .models import PlaylistRole
from .models import PlaylistUserAccess
from .models import PrivilegeLevel
from .models import Resource
from .models import ResourceAccess
from .models import ResourceFile
from .models import ResourceFileKey
from .models import Subtitle
from .models import Track
from .models import User
from .models import UserCourses
from .utils import estimate_current_yearterm

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_MEDIA_DIR = REPO_ROOT / "demo_media"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"usr{n:05d}")
    first_name = factory.Sequence(lambda n: f"User{n}")
    last_name = "Account"
    email = factory.LazyAttribute(lambda user: f"{user.username}@example.test")
    privilege_level = PrivilegeLevel.STUDENT
    is_active = True
    is_staff = False
    is_superuser = False
    password = factory.django.Password("password123")

    class Params:
        admin = factory.Trait(
            is_staff=True,
            is_superuser=True,
        )
        instructor = factory.Trait(privilege_level=PrivilegeLevel.INSTRUCTOR)
        student = factory.Trait(privilege_level=PrivilegeLevel.STUDENT)

    @factory.post_generation
    def lab_assistant(self, create, extracted, **kwargs):
        if not create or not extracted:
            return
        group, _ = Group.objects.get_or_create(name=LAB_ASSISTANT_GROUP_NAME)
        self.groups.add(group)


class LanguageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Language
        django_get_or_create = ("bcp47",)

    language = factory.Sequence(lambda n: f"Language {n}")
    # "qaa".."qtz" is reserved by ISO 639-3 for private use, so these never
    # collide with a real seeded language code.
    bcp47 = factory.Sequence(lambda n: f"q{chr(97 + (n // 26) % 20)}{chr(97 + n % 26)}")


class CourseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Course
        django_get_or_create = ("dept", "catalog_number", "section_number")

    dept = factory.Iterator(["BIO", "FILM", "SPAN", "UNIV"])
    catalog_number = factory.Sequence(lambda n: f"{100 + n:03d}")
    section_number = factory.Sequence(lambda n: f"{(n % 30) + 1:03d}")
    yearterm = factory.LazyFunction(estimate_current_yearterm)


class UserCourseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = UserCourses

    user = factory.SubFactory(UserFactory)
    course = factory.SubFactory(CourseFactory)
    yearterm = factory.LazyFunction(estimate_current_yearterm)


class ResourceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Resource

    name = factory.Sequence(lambda n: f"Demo Resource {n}")
    media_type = Resource.MediaType.VIDEO
    requester_username = factory.Sequence(lambda n: f"req{n:05d}"[:8])
    copyrighted = False
    physical_copy_exists = False
    notes = ""


class ResourceAccessFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ResourceAccess

    user = factory.SubFactory(UserFactory)
    resource = factory.SubFactory(ResourceFactory)


class ResourceFileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ResourceFile

    resource = factory.SubFactory(ResourceFactory)
    version = factory.Sequence(lambda n: f"version_{n:03d}")
    full_video = True
    notes = ""
    file = factory.LazyAttributeSequence(
        lambda _, n: SimpleUploadedFile(
            f"demo-{n:03d}.mp4",
            f"demo video payload {n}".encode(),
            content_type="video/mp4",
        )
    )


class PlaylistFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Playlist
        skip_postgeneration_save = True

    owner = factory.SubFactory(UserFactory, instructor=True)
    name = factory.Sequence(lambda n: f"Demo Playlist {n}")
    published = False
    archived = False
    public = False

    @factory.post_generation
    def courses(self, create, extracted, **kwargs):
        if not create or not extracted:
            return
        self.courses.set(extracted)


class PlaylistUserAccessFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PlaylistUserAccess

    user = factory.SubFactory(UserFactory)
    playlist = factory.SubFactory(PlaylistFactory)
    playlist_role = PlaylistRole.STUDENT


class AnnotationSetFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AnnotationSet
        skip_postgeneration_save = True

    name = factory.Sequence(lambda n: f"Annotation Set {n}")
    resource = factory.SubFactory(ResourceFactory)
    owner = factory.SubFactory(UserFactory, instructor=True)

    @factory.post_generation
    def editors(self, create, extracted, **kwargs):
        if not create or not extracted:
            return
        self.editors.set(extracted)


class TrackFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Track

    annotation_set = factory.SubFactory(AnnotationSetFactory)
    name = factory.Sequence(lambda n: f"Track {n + 1}")
    stack_position = 0


class ContentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Content
        skip_postgeneration_save = True

    title = factory.Sequence(lambda n: f"Demo Content {n}")
    playlist = factory.SubFactory(PlaylistFactory)
    resource_file = factory.SubFactory(ResourceFileFactory)
    resource = factory.SelfAttribute("resource_file.resource")
    annotation_set = None
    description = ""
    allow_definitions = True
    allow_notes = True
    allow_captions = True
    allow_fast_playback = True
    clips_only = False
    published = False

    @factory.post_generation
    def clips(self, create, extracted, **kwargs):
        if not create or not extracted:
            return
        self.clips.set(extracted)

    @factory.post_generation
    def grant_owner_resource_access(self, create, extracted, **kwargs):
        if not create or not self.playlist or not self.get_resource():
            return
        ResourceAccess.objects.get_or_create(
            user=self.playlist.owner,
            resource=self.get_resource(),
        )


class MuteAnnotationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MuteAnnotation

    track = factory.SubFactory(TrackFactory)
    name = factory.Sequence(lambda n: f"Mute {n}")
    start_time = 5.0
    end_time = 12.0
    description = "Mute sensitive dialogue"


class BlankAnnotationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BlankAnnotation

    track = factory.SubFactory(TrackFactory)
    name = factory.Sequence(lambda n: f"Blank {n}")
    start_time = 15.0
    end_time = 20.0
    description = "Hide distracting visual content"
    type = "#"


class CommentAnnotationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CommentAnnotation

    track = factory.SubFactory(TrackFactory)
    name = factory.Sequence(lambda n: f"Comment {n}")
    start_time = 10.0
    end_time = 18.0
    description = "Comment overlay"
    text = "Demo comment"
    top_left_x = 0.0
    top_left_y = 0.0
    bottom_right_x = 100.0
    bottom_right_y = 10.0
    font_size_in_rem = 1.0
    font_color = "ffffff"


class ClipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Clip

    track = factory.SubFactory(TrackFactory)
    name = factory.Sequence(lambda n: f"Clip {n}")
    start_time = 0.0
    end_time = 15.0
    description = ""


class SubtitleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Subtitle

    resource = factory.SubFactory(ResourceFactory)
    owner = factory.SubFactory(UserFactory, instructor=True)
    language = factory.SubFactory(LanguageFactory)
    name = factory.Sequence(lambda n: f"Subtitle Track {n}")
    subtitles_file = factory.LazyAttributeSequence(
        lambda _, n: SimpleUploadedFile(
            f"subtitle-{n:03d}.vtt",
            b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nDemo subtitle\n",
            content_type="text/vtt",
        )
    )
    is_original = True
    words = ""


class ResourceFileKeyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ResourceFileKey

    user = factory.SubFactory(UserFactory)
    resource_file = factory.SubFactory(ResourceFileFactory)
