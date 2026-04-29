import json
import logging
import os

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db import transaction
from django.utils import timezone
import xxhash

from .utils import seconds2hms

HMS_VALIDATOR = RegexValidator(
    regex=r"^\d{1,2}:[0-5]\d:[0-5]\d(?:\.\d{1,4})?$",
    message="Time must be in H:MM:SS format (e.g., 1:23:45.67 or 12:34:56.78)",
    code="invalid_time_format",
)

logger = logging.getLogger(__name__)


class PrivilegeLevel(models.IntegerChoices):
    ADMIN = 0
    LAB_ASSISTANT = 1
    INSTRUCTOR = 2
    STUDENT = 3


class CollectionRole(models.IntegerChoices):
    INSTRUCTOR = 0
    TA = 1
    STUDENT = 2
    AUDITOR = 3


class Resource(models.Model):
    class MediaType(models.TextChoices):
        TEXT = ("txt", "Text")
        VIDEO = ("vid", "Video")
        WEB = ("www", "Web")
        AUDIO = ("aud", "Audio")

    name = models.CharField(max_length=255, unique=True)
    media_type = models.CharField(max_length=3, choices=MediaType.choices, blank=True)
    requester_netid = models.CharField(max_length=8)
    copyrighted = models.BooleanField(default=True)
    physical_copy_exists = models.BooleanField(default=False)
    views = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name}"


class CustomUserManager(BaseUserManager):
    def create_user(
        self,
        netid,
        byu_id=None,
        privilege_level=PrivilegeLevel.STUDENT,
        password=None,
        privilege_level_override=None,
        **extra_fields,
    ):
        user = self.model(
            netid=netid,
            privilege_level=privilege_level,
            privilege_level_override=privilege_level_override,
            **extra_fields,
        )
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, netid, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("privilege_level", PrivilegeLevel.ADMIN)

        return self.create_user(netid=netid, password=password, **extra_fields)


class User(AbstractUser):
    netid = models.CharField(max_length=8, unique=True)
    byu_id = models.CharField(max_length=9, blank=True, null=True)
    USERNAME_FIELD = "netid"
    REQUIRED_FIELDS = []
    privilege_level = models.IntegerField(
        choices=PrivilegeLevel.choices, default=PrivilegeLevel.STUDENT
    )
    privilege_level_override = models.IntegerField(
        choices=PrivilegeLevel.choices, blank=True, null=True
    )
    resources = models.ManyToManyField(
        Resource, through="ResourceAccess", related_name="users"
    )
    accessible_collections = models.ManyToManyField(
        "Collection", through="CollectionUserAccess", related_name="users"
    )
    courses = models.ManyToManyField("Course", through="UserCourses", blank=True)

    objects = CustomUserManager()

    def __str__(self):
        return f"{self.first_name} {self.last_name} | {self.netid}"

    def to_dict(self):
        return {
            "netid": self.netid,
            "byuid": self.byu_id,
            "first_name": self.first_name,
            "last_name": self.last_name,
        }

    @property
    def is_admin(self):
        return self.privilege_level == PrivilegeLevel.ADMIN

    def can_view_content(self, content):
        # owners and admins should have view permission even if the collection is not published
        if content.collection.owner == self:
            return True
        if self.is_admin or self.is_superuser or self.is_staff:
            return True
        if content.collection.published:
            if CollectionUserAccess.objects.filter(
                user=self, collection=content.collection
            ).exists():
                return True
            # TODO Check course enrollment
        return False

    def get_resource_filekey(self, content):
        """Get or create a FileKey for the given content."""
        if self.can_view_content(content):
            resource_file_key = ResourceFileKey.objects.filter(
                resource_file=content.resource_file, user=self
            ).first()
            if not resource_file_key:
                resource_file_key = ResourceFileKey.objects.create(
                    resource_file=content.resource_file, user=self
                )
                resource_file_key.save()
            return resource_file_key
        else:
            return None


class ResourceAccess(models.Model):  # "through" model
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE)
    last_verified = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "resource")

    def __str__(self):
        return f"{self.user.netid} | {self.resource.name} | {self.id}"


class Collection(models.Model):
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="collections_owned"
    )
    published = models.BooleanField(default=False)
    archived = models.BooleanField(default=False)
    public = models.BooleanField(default=False)
    courses = models.ManyToManyField("Course", related_name="collections", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} | {self.owner} | {self.id}"

    class Meta:
        unique_together = ("name", "owner")

    def get_instructors_and_tas(self):
        """Get all users with instructor or TA access to this collection."""
        instructor_tas = CollectionUserAccess.objects.filter(
            collection=self,
            collection_role__in=[CollectionRole.INSTRUCTOR, CollectionRole.TA],
        ).select_related("user")
        return [access.user for access in instructor_tas]


class CollectionUserAccess(models.Model):  # "through" model
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE)
    collection_role = models.IntegerField(
        choices=CollectionRole.choices, default=CollectionRole.STUDENT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "collection")

    def __str__(self):
        return f"{self.user.netid} | {self.collection.name}"


def validate_media_file(file):
    """Validate that uploaded file is video, audio, or image."""
    valid_extensions = {
        # Video
        ".mp4",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".mkv",
        ".m4v",
        # Audio
        ".mp3",
        ".wav",
        ".flac",
        ".aac",
        ".ogg",
        ".wma",
        ".m4a",
        # Image
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".svg",
    }

    ext = os.path.splitext(file.name)[1].lower()
    if ext not in valid_extensions:
        raise ValidationError(
            f"File type not supported. Must be video, audio, or image file. Got: {ext}"
        )


def _calculate_checksum_for_file(file):
    """Calculate and return the xxhash64 checksum of a file-like object."""
    if not file:
        return None
    file.seek(0)
    file_hash = xxhash.xxh64()
    for chunk in iter(lambda: file.read(4096), b""):
        file_hash.update(chunk)
    file.seek(0)  # Reset the file pointer for subsequent reads
    return file_hash.hexdigest()


def validate_unique_checksum(file):
    """Validator to ensure the uploaded file's content is unique."""
    new_checksum = _calculate_checksum_for_file(file)
    if new_checksum:
        query = ResourceFile.objects.filter(checksum=new_checksum)
        if file.instance.pk:
            query = query.exclude(pk=file.instance.pk)

        existing_file = query.first()
        if existing_file:
            raise ValidationError(
                f"A file with the same content already exists: {existing_file.file.name}"
            )


def file_upload_path(instance, filename):
    """Generate upload path: media/<resource name>/<version>.<ext>"""
    if instance.resource and instance.version:
        ext = os.path.splitext(filename)[1]
        return f"{instance.resource.name}/{instance.version}{ext}"
    return filename


class ResourceFile(models.Model):
    file = models.FileField(
        upload_to=file_upload_path,
        validators=[validate_media_file, validate_unique_checksum],
    )
    resource = models.ForeignKey(
        Resource, on_delete=models.CASCADE, related_name="resource_files"
    )
    audio_language = models.ForeignKey(
        "Language",
        on_delete=models.SET_NULL,
        default=None,
        null=True,
        blank=True,
        related_name="files_with_audio_language",
    )
    burned_in_subtitles_language = models.ForeignKey(
        "Language",
        on_delete=models.SET_NULL,
        default=None,
        null=True,
        blank=True,
        related_name="files_with_burned_in_subtitles",
    )
    version = models.CharField(max_length=100)
    full_video = models.BooleanField(
        default=True,
        help_text="Does this file contain the entire work (i.e., not just a clip?)",
    )
    notes = models.TextField(blank=True)
    checksum = models.CharField(
        max_length=16, blank=True, editable=False, unique=True, null=True
    )
    checksum_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def delete(self, *args, **kwargs):
        """Delete the file from the filesystem when the model is deleted."""
        if self.file:
            self.file.delete(save=False)
        super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        """Generate checksum before saving."""
        if self.file and not self.checksum:
            self.checksum = _calculate_checksum_for_file(self.file)
            self.checksum_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.file} | {self.resource.name}"

    class Meta:
        unique_together = ("resource", "version")


class Clip(models.Model):
    resource = models.ForeignKey(
        Resource, on_delete=models.CASCADE, related_name="clips"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="clips"
    )
    name = models.CharField(max_length=255)
    start_time = models.FloatField(default=0.0)
    end_time = models.FloatField(default=0.0)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} | {self.start_time}-{self.end_time} | {self.resource.name} | {self.id}"

    def can_edit(self, user):
        """Check if user can edit this clip."""
        return self.owner == user or user.is_staff or user.is_superuser or user.is_admin

    def clone_for_user(self, user):
        """Create a copy of this clip owned by a different user."""
        return Clip.objects.create(
            resource=self.resource,
            owner=user,
            name=self.name,
            start_time=self.start_time,
            end_time=self.end_time,
            description=self.description,
        )

    def save(self, *args, **kwargs):
        self.start_time = round(float(self.start_time or 0), 2)
        self.end_time = round(float(self.end_time or 0), 2)
        super().save(*args, **kwargs)


class AnnotationSet(models.Model):
    """
    A collection of annotations for a Resource.
    Multiple contents can use the same AnnotationSet.
    """

    name = models.CharField(max_length=255)
    resource = models.ForeignKey(
        "Resource", on_delete=models.CASCADE, related_name="annotation_sets"
    )
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="owned_annotation_sets"
    )
    editors = models.ManyToManyField(
        User, related_name="editable_annotation_sets", blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["name", "resource"]]
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.owner.first_name} {self.owner.last_name}) | {self.resource.name}"

    def can_edit(self, user):
        """Check if user can edit this annotation set."""
        return user == self.owner or user in self.editors.all() or user.is_admin

    def can_be_viewed_by(self, user):
        """Check if user can view this annotation set (through any content using the resource)."""
        return Content.objects.filter(resource_file__resource=self.resource).filter(
            collection__owner=user
        ).exists() or self.can_edit(user)

    @classmethod
    def create_for_content(
        cls,
        content,
        user,
        set_name=None,
        annotation_set_json=None,
        annotation_set_id_to_copy=None,
    ):
        """
        Create a new AnnotationSet for a content's resource.
        Automatically adds collection owner and instructor/TAs as editors.
        """
        try:
            collection = content.collection
            resource = content.resource_file.resource if content.resource_file else None

            if not resource:
                raise ValueError(
                    "Content must have a file with a resource to create an AnnotationSet"
                )

            # Create annotation set with user as owner
            if set_name is not None and set_name != "":
                name = set_name
            else:
                name = f"{user.get_full_name()}'s Annotations"

            # check for set with the same name and resource combination
            existing_count = cls.objects.filter(
                name__startswith=name, resource=resource
            ).count()
            if existing_count > 0:
                name = f"{name} ({existing_count + 1})"

            annotation_set = cls.objects.create(
                name=name, resource=resource, owner=user
            )

            # Add all collection instructors/TAs as editors
            instructors_and_tas = collection.get_instructors_and_tas()
            annotation_set.editors.add(*instructors_and_tas)

            # create new annotations if provided (if importing from json, for example)
            if annotation_set_json is not None and type(annotation_set_json) == str:
                annotation_types = {
                    "SkipAnnotation": SkipAnnotation,
                    "MuteAnnotation": MuteAnnotation,
                    "BlankAnnotation": BlankAnnotation,
                    "PauseAnnotation": PauseAnnotation,
                    "BlurAnnotation": BlurAnnotation,
                    "CommentAnnotation": CommentAnnotation,
                }

                annotation_set_data = json.loads(annotation_set_json)
                for track in annotation_set_data.get("tracks", []):
                    Track.from_import(track, annotation_set)

                for annotation in annotation_set_data.get("annotations", []):
                    track = annotation_set.tracks.get(name=annotation.get("track_name"))
                    try:
                        annotation_class = annotation_types[annotation["class_type"]]
                        annotation_class.from_import(annotation, track)
                    except Exception as e:
                        logger.error(
                            f"Failed to import annotation. Annotation type: {annotation_class}. Exception: {e}"
                        )
                        continue

            # copying from pre-existing annotation set
            if annotation_set_json is None and annotation_set_id_to_copy is not None:
                annotation_set_to_copy = AnnotationSet.objects.get(
                    pk=annotation_set_id_to_copy
                )
                if set_name is None or set_name == "":
                    annotation_set.name = (
                        annotation_set_to_copy.name + f" (copied by {user.__str__})"
                    )
                annotations = (
                    annotation_set_to_copy.get_active_annotations_from_tracks()
                )
                for annotation in annotations:
                    annotation.copy_to_new_annotation_set(annotation_set)

            return annotation_set
        except Exception as e:
            logger.error(f"Failed to create annotation set. Exception: {e}")
            return None

    def get_tracks(self):
        """
        Get all tracks as they are
        """
        return sorted(self.tracks.all(), key=lambda track: track.stack_position)

    def get_highest_stack_position(self):
        track_with_highest_position_value = (
            self.tracks.all().order_by("stack_position").last()
        )
        return track_with_highest_position_value.stack_position

    def get_active_annotations_from_tracks(self):
        """
        Get all currently active annotations across all tracks.
        """
        annotations = []
        for track in list(self.tracks.all()):
            annotations.extend(track.get_active_annotations())
        return sorted(annotations, key=lambda a: a.start_time)

    def to_player_json(self):
        """Export all active annotations in this set for the AnnotationPlayer."""
        return {
            "tracks": [track.to_player_json() for track in self.get_tracks()],
            "annotations": [
                annotation.to_player_json()
                for annotation in self.get_active_annotations_from_tracks()
            ],
        }


class Track(models.Model):
    annotation_set = models.ForeignKey(
        AnnotationSet,
        on_delete=models.CASCADE,
        related_name="tracks",
        null=False,
        blank=False,
    )
    name = models.CharField(max_length=50, default="Track 1")
    stack_position = models.IntegerField(default=0)

    def get_active_annotations(self):
        """
        Get all currently active annotations across all types.
        Active annotations are the current state (tip of linked list).
        """
        annotations = []
        for model_class in [
            SkipAnnotation,
            MuteAnnotation,
            BlankAnnotation,
            PauseAnnotation,
            BlurAnnotation,
            CommentAnnotation,
        ]:
            annotations.extend(model_class.objects.filter(track=self, active=True))
        return sorted(annotations, key=lambda a: a.start_time)

    def is_final_stack_position(self):
        return self.annotation_set.get_highest_stack_position() == self.stack_position

    def to_player_json(self):
        """Export all active annotations in this set for the AnnotationPlayer."""
        return {"id": self.pk, "name": self.name, "stack_position": self.stack_position}

    def copy_to_new_annotation_set(self, annotation_set):
        # check if a track with this name exists in the new annotation set
        track_query_set = Track.objects.filter(
            annotation_set=annotation_set, name=self.name
        )
        if track_query_set.count() > 0:
            return track_query_set.first()
        else:
            new_track = Track.objects.create(
                annotation_set=annotation_set,
                name=self.name,
                stack_position=self.stack_position,
            )
            return new_track

    @classmethod
    def from_import(cls, imported_data, annotation_set):
        return cls.objects.create(
            annotation_set=annotation_set,
            name=imported_data.get("name", ""),
            stack_position=imported_data.get("stack_position", 0),
        )


class Content(models.Model):
    title = models.CharField(max_length=255)
    collection = models.ForeignKey(
        Collection,
        on_delete=models.CASCADE,
        related_name="contents",
        null=True,
        blank=True,
    )
    resource_file = models.ForeignKey(
        ResourceFile,
        on_delete=models.CASCADE,
        related_name="contents",
        null=True,
        blank=True,
    )
    annotation_set = models.ForeignKey(
        AnnotationSet,
        on_delete=models.SET_NULL,
        related_name="contents",
        null=True,
        blank=True,
    )
    url = models.URLField(max_length=500, blank=True, null=True)
    description = models.TextField(blank=True)
    allow_definitions = models.BooleanField(default=True)
    allow_notes = models.BooleanField(default=True)
    allow_captions = models.BooleanField(default=True)
    views = models.IntegerField(default=0, editable=False)
    published = models.BooleanField(default=False)
    words = models.TextField(blank=True)
    clips = models.ManyToManyField(
        Clip,
        related_name="contents",
        blank=True,
        limit_choices_to=models.Q(resource=models.F("resource")),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("collection", "title")

    def __str__(self):
        return f"{self.title} | {self.collection.name} | {self.id}"

    def get_available_annotation_sets(self):
        """Get all AnnotationSets available for this content's resource."""
        if not self.resource_file or not self.resource_file.resource:
            return AnnotationSet.objects.none()
        return AnnotationSet.objects.filter(resource=self.resource_file.resource)

    def get_clips_json(self):
        """
        Get all clips as list of dictionaries for the AnnotationPlayer.
        """
        clips_data = []
        for clip in self.clips.all():
            start = float(clip.start_time or 0)
            end = float(clip.end_time or 0)
            clips_data.append(
                {
                    "start": start,  # seconds for backend/player logic
                    "end": end,
                    "start_hms": seconds2hms(start),  # "0:00:12.34" for display
                    "end_hms": seconds2hms(end),
                    "label": clip.name,
                }
            )
        return clips_data

    def get_subtitles(self):
        """
        Get all subtitles as list of dicts for the AnnotationPlayer.
        Each dict has the following keys:
            - 'srclang'
            - 'vtt' or 'url'
            - 'label'
        """
        sub_objs = Subtitle.objects.filter(resource=self.resource_file.resource)
        subtitles = [
            {
                "id": sub.pk,
                "srclang": sub.language.lang_tag,
                "vtt": sub.subtitles_file.read().decode("utf-8"),
                "name": sub.name,
            }
            for sub in sub_objs
        ]
        return subtitles

    def get_player_json(self):
        """
        Generate complete JSON data for AnnotationPlayer.loadData().
        Returns a dict with 'annotations' and 'clips' keys, each containing JSON strings.
        """
        annotation_set_json = (
            self.annotation_set.to_player_json()
            if self.annotation_set
            else {"annotations": [], "tracks": []}
        )
        return {
            "annotations": annotation_set_json["annotations"],
            "tracks": annotation_set_json["tracks"],
            "clips": self.get_clips_json(),
            "subtitleTracks": self.get_subtitles(),
        }


class ImportantWord(models.Model):
    content = models.ForeignKey(
        Content, on_delete=models.CASCADE, null=False, blank=False
    )
    word = models.CharField(null=False, blank=True, max_length=50)
    translation = models.CharField(null=False, blank=True, max_length=100)


class BaseAnnotation(models.Model):
    """
    Base class for all annotation types.
    Implements linked list for undo/redo functionality.
    Undo/redo is per-annotation: each annotation has its own version history.
    """

    name = models.CharField(max_length=255, blank=True)
    track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_annotations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    start_time = models.FloatField(default=0.0)
    end_time = models.FloatField(default=0.0)

    # Linked list pointers for undo/redo
    prev = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_prev_relation",
        null=True,
        blank=True,
    )
    next = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_next_relation",
        null=True,
        blank=True,
    )
    description = models.TextField(
        blank=False,
        null=False,
        help_text="Short description of what content this annotation covers.",
    )

    # Only active annotations are visible/used
    active = models.BooleanField(default=True)

    class Meta:
        abstract = True

    @property
    def annotation_type(self):
        """Return the annotation type string (e.g., 'skip', 'pause')."""
        type_string = self.__class__.__name__.replace("Annotation", "").lower()
        if type_string == "blur":
            type_string = "censor"
        return type_string

    def calculate_position(self):
        """Calculate visual position on timeline."""
        return {
            "left": "0%",
            "width": "0%",
            "start": self.start_time,
            "end": self.end_time,
        }

    def to_player_json(self):
        """Convert annotation to JSON format for video player."""
        start = float(self.start_time)
        end = float(self.end_time)
        return {
            "id": self.pk,
            "name": self.name,
            "type": self.annotation_type,
            "class_type": self.__class__.__name__,
            "start": start,
            "end": end,
            "start_display": seconds2hms(start),
            "end_display": seconds2hms(end),
            "label": self.name,
            "track_name": self.track.name,
            "description": self.description,
            "active": self.active,
        }

    def copy_to_new_annotation_set(self, annotation_set):
        new_track = self.track.copy_to_new_annotation_set(annotation_set)
        annotation_types = {
            "SkipAnnotation": SkipAnnotation,
            "MuteAnnotation": MuteAnnotation,
            "BlankAnnotation": BlankAnnotation,
            "PauseAnnotation": PauseAnnotation,
            "BlurAnnotation": BlurAnnotation,
            "CommentAnnotation": CommentAnnotation,
        }
        new_annotation = annotation_types[self.__class__.__name__].objects.create(
            name=self.name,
            track=new_track,
            start_time=self.start_time,
            end_time=self.end_time,
            description=self.description,
            active=self.active,
        )
        return new_annotation

    @classmethod
    def from_import(cls, imported_data, track):
        return cls.objects.create(
            name=imported_data.get("name", ""),
            track=track,
            start_time=imported_data.get("start", 0.0),
            end_time=imported_data.get("end", 0.0),
            description=imported_data.get("description", ""),
            active=True,
        )

    @transaction.atomic
    def edit(self, **update_fields):
        """
        Edit this annotation by creating a new version in the linked list.
        """
        # Step 1: Mark this annotation as inactive
        self.active = False
        self.save()

        # Step 2: Recursively delete any next versions (clear redo history)
        self._delete_next_chain()

        # Step 3: Create new version with updated fields
        new_data = {
            field.name: getattr(self, field.name)
            for field in self._meta.fields
            if field.name
            not in ["id", "created_at", "updated_at", "prev", "next", "active"]
        }

        # Apply updates
        new_data.update(update_fields)

        # Create new annotation
        new_annotation = self.__class__.objects.create(
            **new_data, prev=self, next=None, active=True
        )

        # Step 4: Link new annotation as this annotation's next
        self.next = new_annotation
        self.save()

        return new_annotation

    def _delete_next_chain(self):
        """Recursively delete all next annotations in the chain."""
        if self.next is not None:
            next_annotation = self.next
            self.next = None
            self.save()

            # Recursively delete the rest of the chain
            next_annotation._delete_next_chain()
            next_annotation.delete()

    @transaction.atomic
    def delete_with_history(self):
        """
        Mark this annotation as deleted by creating an inactive version.
        """
        # Mark current as inactive
        self.active = False
        self.save()

        # Clear any redo history
        self._delete_next_chain()

    @transaction.atomic
    def undo(self):
        """
        Undo this annotation: revert to the previous version in the linked list.
        Returns the previous version if undo is possible, else None.
        """
        if self.prev is None:
            return None
        self.active = False
        self.save()
        self.prev.active = True
        self.prev.save()
        return self.prev

    @transaction.atomic
    def redo(self):
        """
        Redo this annotation: move forward to the next version in the linked list.
        Returns the next version if redo is possible, else None.
        """
        if self.next is None:
            return None
        self.active = False
        self.save()
        self.next.active = True
        self.next.save()
        return self.next

    def save(self, *args, **kwargs):
        self.start_time = round(float(self.start_time or 0), 2)
        self.end_time = round(float(self.end_time or 0), 2)
        super().save(*args, **kwargs)


class SkipAnnotation(BaseAnnotation):
    """Skip annotation - standard time range. Allows optional message to be displayed at the beginning of a skip."""

    message = models.TextField(max_length=255, blank=True)

    def calculate_position(self):
        return {
            "left": "0%",
            "width": "2px",
            "start": self.start_time,
            "end": self.end_time,
        }

    def to_player_json(self):
        data = super().to_player_json()  # includes start/end + display strings
        data["type"] = "skip"  # keep/ensure type
        data["message"] = self.message
        return data

    def copy_to_new_annotation_set(self, annotation_set):
        new_annotation = super().copy_to_new_annotation_set(annotation_set)
        new_annotation.message = self.message
        new_annotation.save()
        return new_annotation

    @classmethod
    def from_import(cls, imported_data, track):
        obj = super().from_import(imported_data, track)
        obj.message = imported_data.get("message", "")
        obj.save()
        return obj


class MuteAnnotation(BaseAnnotation):
    """Mute annotation - standard time range."""

    @classmethod
    def from_import(cls, imported_data, track):
        return super().from_import(imported_data, track)


class BlankAnnotation(BaseAnnotation):
    """Blank annotation - standard time range."""

    type = models.CharField(
        max_length=10,
        choices=[
            ("k", "Black"),  # <video> CSS filter: brightness(0)
            ("#", "Blur"),  # <video> CSS filter: blur(30px)
            ("w", "White"),  # <video> CSS filter: brightness(10)
        ],
        default="k",
    )

    def copy_to_new_annotation_set(self, annotation_set):
        new_annotation = super().copy_to_new_annotation_set(annotation_set)
        new_annotation.type = self.type
        new_annotation.save()
        return new_annotation

    @classmethod
    def from_import(cls, imported_data, track):
        obj = super().from_import(imported_data, track)
        obj.type = imported_data.get("type", "k")
        obj.save()
        return obj


class PauseAnnotation(BaseAnnotation):
    """Pause annotation - point in time, not a range."""

    message = models.TextField(max_length=255, blank=True)

    def calculate_position(self):
        """Override: pause is a point marker, not a range."""
        return {
            "left": "0%",
            "width": "2px",
            "start": self.start_time,
            "end": self.start_time,  # TODO: Would None be better?
        }

    def to_player_json(self):
        """pause needs a start time even though it doesnt have an end time. This is because
        of how the AnnotationPlayer parses annotations for Y-video"""
        t = float(self.start_time or 0)
        data = super().to_player_json()
        data["message"] = self.message
        return data

    def copy_to_new_annotation_set(self, annotation_set):
        new_annotation = super().copy_to_new_annotation_set(annotation_set)
        new_annotation.message = self.message
        new_annotation.save()
        return new_annotation

    @classmethod
    def from_import(cls, imported_data, track):
        obj = super().from_import(imported_data, track)
        obj.message = imported_data.get("message", "")
        obj.save()
        return obj


class CommentAnnotation(BaseAnnotation):
    """Comment annotation with text and position."""

    text = models.TextField(default="Your comment here")
    x = models.FloatField(default=50.0)
    y = models.FloatField(default=50.0)

    def to_player_json(self):
        """Override: include text and coordinates."""
        data = super().to_player_json()
        data.update(
            {
                "text": self.text,
                "x": self.x,
                "y": self.y,
            }
        )
        return data

    def copy_to_new_annotation_set(self, annotation_set):
        new_annotation = super().copy_to_new_annotation_set(annotation_set)
        new_annotation.text = self.text
        new_annotation.x = self.x
        new_annotation.y = self.y
        new_annotation.save()
        return new_annotation

    @classmethod
    def from_import(cls, imported_data, track):
        obj = super().from_import(imported_data, track)
        obj.text = imported_data.get("text", "Your comment here")
        obj.x = imported_data.get("x", 50.0)
        obj.y = imported_data.get("y", 50.0)
        obj.save()
        return obj


class BlurAnnotation(BaseAnnotation):
    def to_player_json(self):
        """Override: include positions data."""
        data = super().to_player_json()
        positions_query_set = self.positions.all().order_by("time")
        positions = []
        for position in positions_query_set:
            positions.append(
                {
                    "id": position.pk,
                    "time": position.time,
                    "x": position.x,
                    "y": position.y,
                    "width": position.width,
                    "height": position.height,
                    "blur_amount": position.blur_amount,
                    "type": position.type,
                }
            )
        data.update({"positions": positions, "type": "censor"})
        return data

    def get_position_locators(self):
        positions = self.positions.all()
        locators = []
        normalized_duration = self.end_time - self.start_time
        if normalized_duration <= 0:
            return locators
        for position in positions:
            relative_time = position.time - self.start_time
            locators.append(
                {
                    "id": position.pk,
                    "time": position.time,
                    "is_not_start": position.time != 0,
                    "relative_location": round(
                        (relative_time / normalized_duration) * 100, 2
                    ),
                }
            )
        return locators

    def remove_positions_outside_of_timebox(self):
        positions = list(self.positions.all())
        position_index = len(positions) - 1
        while position_index >= 0:
            position = positions[position_index]
            if position.time != 0 and (
                position.time < self.start_time or position.time > self.end_time
            ):
                position.delete()
            position_index = position_index - 1

    def copy_to_new_annotation_set(self, annotation_set):
        new_annotation = super().copy_to_new_annotation_set(annotation_set)
        positions = self.positions.all()
        for position in positions:
            BlurAnnotationPosition.objects.create(
                blur_annotation=new_annotation,
                time=position.time,
                x=position.x,
                y=position.y,
                width=position.width,
                height=position.height,
                blur_amount=position.blur_amount,
                type=position.type,
            )
        new_annotation.save()
        return new_annotation

    @classmethod
    def from_import(cls, imported_data, track):
        obj = super().from_import(imported_data, track)
        for position in imported_data.get("positions", []):
            BlurAnnotationPosition.objects.create(
                blur_annotation=obj,
                time=position.get("time"),
                x=position.get("x"),
                y=position.get("y"),
                width=position.get("width"),
                height=position.get("height"),
                blur_amount=position.get("blur_amount", 60),
                type=position.get("type", "blur"),
            )
        return obj


class BlurAnnotationPosition(models.Model):
    blur_annotation = models.ForeignKey(
        BlurAnnotation,
        on_delete=models.CASCADE,
        null=False,
        blank=False,
        related_name="positions",
    )
    time = models.FloatField(null=False, blank=False)
    x = models.FloatField(null=False, blank=False)
    y = models.FloatField(null=False, blank=False)
    width = models.FloatField(null=False, blank=False)
    height = models.FloatField(null=False, blank=False)
    blur_amount = models.IntegerField(null=False, blank=False, default=60)
    type = models.TextField(null=False, blank=False, default="blur")

    @classmethod
    def validate(cls, data_dict):
        try:
            if data_dict["time"] < cls.blur_annotation.start_time:
                return (False, "Position cannot be before the annotation starts.")
            if data_dict["time"] > cls.blur_annotation.end_time:
                return (False, "Position cannot be after the annotation ends.")
            if (
                data_dict["x"] < 0
                or data_dict["y"] < 0
                or data_dict["width"] < 0
                or data_dict["height"] < 0
            ):
                return (False, "Position x, y, width, and height cannot be less than 0")
            return True, None
        except Exception as e:
            return False, f"Invalid position: {e}"


class Course(models.Model):
    dept = models.CharField(
        max_length=5,
        validators=[
            RegexValidator(
                regex=r"^[A-Z ]{2,5}$",
                message="Department must be 2 to 5 uppercase letters or spaces.",
                code="invalid_dept",
            )
        ],
    )
    catalog_number = models.CharField(
        max_length=4,
        validators=[
            RegexValidator(
                regex=r"^\d{3}R?$",
                message="Catalog number must be a 3-digit number (and optional 'R' suffix).",
                code="invalid_catalog_number",
            )
        ],
    )

    section_number = models.CharField(
        max_length=3,
        validators=[
            RegexValidator(
                regex=r"^\d{3}$",
                message="Section number must be 1 to 3 digits.",
                code="invalid_section_number",
            )
        ],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("dept", "catalog_number", "section_number")
        ordering = ["dept", "catalog_number", "section_number"]

    def __str__(self):
        return f"{self.dept} {self.catalog_number}-{self.section_number}"


class UserCourses(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    yearterm = models.CharField(max_length=5, blank=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def display_yearterm(self):
        if self.yearterm is None:
            return ""
        year_str = self.yearterm[:4]
        term_str = self.yearterm[4:]
        term_map = {"1": "Winter", "3": "Spring", "4": "Summer", "5": "Fall"}
        try:
            term_name = term_map[term_str]
        except KeyError:
            logger.error(
                f"UserCourse has a missing or invalid yearterm. UserCourseId: {self.pk}, yearterm: {self.yearterm}"
            )
        except Exception:
            logger.error(
                f"An error has occurred while displaying the yearterm for the following UserCousreId: {self.pk}"
            )
        return f"{term_name} {year_str}"

    def __str__(self):
        return f"{self.course.dept} {self.course.catalog_number} Section {self.course.section_number} {self.display_yearterm()}"


class Language(models.Model):
    language = models.CharField(max_length=60, unique=True, blank=False, null=False)
    # TODO ensure that these are ISO 639-1 (2002) or a three-letter code from ISO 639-2 (1998), ISO 639-3 (2007) or ISO 639-5 (2008)
    lang_tag = models.CharField(max_length=10, unique=True, blank=False, null=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["language"]

    def __str__(self):
        return f"{self.language} ({self.lang_tag})"


def subtitle_file_upload_path(instance, filename):
    """Generate upload path to media/<resource name>/subtitles/<filename>"""
    if isinstance(instance, Subtitle):
        return f"{instance.resource.name}/subtitles/{filename}"


def subtitle_temp_file_upload_path(instance):
    """Generate upload path to media/<resource name>/subtitles/<filename>"""
    if isinstance(instance, Subtitle):
        return f"{instance.resource.name}/subtitles/{instance.name}_temp.vtt"


def validate_subtitle_file(file):
    """Ensure filetype is .vtt or .srt"""
    file_name_split = os.path.splitext(file.name)
    file_ext = file_name_split[1]
    if file_ext != ".vtt" and file_ext != ".srt":
        raise ValidationError(
            f"Subtitles must be .vtt or .srt format. Format provided: {file_ext}"
        )


class Subtitle(models.Model):
    resource = models.ForeignKey(
        Resource, on_delete=models.CASCADE, related_name="subtitles"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subtitles"
    )
    language = models.ForeignKey(
        Language, on_delete=models.CASCADE, related_name="subtitles"
    )
    name = models.CharField(max_length=255)
    subtitles_file = models.FileField(
        upload_to=subtitle_file_upload_path,
        validators=[validate_subtitle_file],
    )
    subtitles_temp_file = models.FileField(
        upload_to=subtitle_temp_file_upload_path,
        validators=[validate_subtitle_file],
        null=True,
        blank=True,
    )
    is_original = models.BooleanField(null=False, blank=False, default=False)
    words = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} | {self.language.language} | {self.resource.name} | {self.owner.first_name} {self.owner.last_name} | {self.id}"

    class Meta:
        unique_together = ("resource", "owner", "language", "name")


class ResourceFileKey(models.Model):  # "through" model
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="resource_file_keys",
    )
    resource_file = models.ForeignKey(
        ResourceFile, on_delete=models.CASCADE, related_name="resource_file_keys"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.first_name} {self.user.last_name} | {self.resource_file.resource.name} | {self.resource_file.version} | {self.id}"


class Email(models.Model):
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="emails"
    )
    sender_email = models.EmailField(max_length=255)
    recipients = models.JSONField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.sender.netid} | {self.subject} | {self.pk}"


class AuthToken(models.Model):
    token = models.CharField(max_length=150, null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ResourceContentIntakeRequest(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Resource-specific fields
    resource = models.ForeignKey(
        "Resource", on_delete=models.CASCADE, related_name="content_requests"
    )
    # Checkout information
    checked_out_from_hbll = models.BooleanField(default=False)
    checked_out_from_other_byu_library = models.BooleanField(default=False)
    checked_out_from_non_byu_library = models.BooleanField(default=False)
    # Purpose of use fields
    is_for_course_use = models.BooleanField(default=False)
    is_for_ic_use = models.BooleanField(default=False)
    # Content advisory fields
    violence_or_blood_and_gore = models.BooleanField(default=False)
    nudity_or_sexual_content = models.BooleanField(default=False)
    profanity_or_vulgarity = models.BooleanField(default=False)
    self_harm_or_suicide = models.BooleanField(default=False)
    drug_use = models.BooleanField(default=False)

    def __str__(self):
        return f"Content request for {self.resource.title}"
