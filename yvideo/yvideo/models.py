import uuid

from django.conf import settings
from django.db import models


# TODO: DO NOT COMMIT/MERGE until all on_delete behaviors are correct.

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
        TEXT = ('txt', 'Text')
        VIDEO = ('vid', 'Video')
        WEB = ('www', 'Web')
        AUDIO = ('aud', 'Audio')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    media_type = models.CharField(max_length=3, choices=MediaType.choices, blank=True)
    requester_netid = models.CharField(max_length=8)
    copyrighted = models.BooleanField(default=True)
    physical_copy_exists = models.BooleanField(default=False)
    date_ownership_validated = models.DateTimeField(auto_now_add=True)
    views = models.IntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Rsrc({self.name} | {self.id})"


class User(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    netid = models.CharField(max_length=8, unique=True)
    person_id = models.CharField(max_length=9, blank=True, null=True)
    last_login = models.DateTimeField(null=True, blank=True)
    privilege_level = models.IntegerField(choices=PrivilegeLevel.choices, default=PrivilegeLevel.STUDENT)
    privilege_level_override = models.IntegerField(choices=PrivilegeLevel.choices, blank=True, null=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255, blank=True)
    resources = models.ManyToManyField(Resource, through='ResourceAccess', related_name='users')
    collections = models.ManyToManyField('Collection', through='CollectionUserAccess', related_name='users')
    courses = models.ManyToManyField('Course', related_name='users', blank=True)
    file_keys = models.ManyToManyField('File', through='FileKey', related_name='users')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"User({self.name} | {self.netid} | {self.id})"


class ResourceAccess(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE)
    last_verified = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('user', 'resource')


class Collection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='collections_owned')
    published = models.BooleanField(default=False)
    archived = models.BooleanField(default=False)
    public = models.BooleanField(default=False)
    courses = models.ManyToManyField('Course', related_name='collections', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class CollectionUserAccess(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE)
    collection_role = models.IntegerField(choices=CollectionRole.choices, default=CollectionRole.STUDENT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'collection')

    def __str__(self):
        return f"CollectionUserAccess({self.user.netid} | {self.collection.name})"


class File(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    path = models.FilePathField(settings.MEDIA_DIR, max_length=500)
    version = models.CharField(max_length=100)
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='files', null=True, blank=True)
    full_video = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"File({self.filepath} | {self.id}"

class Annotation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name='annotations')
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='annotations')
    name = models.CharField(max_length=255, blank=True)
    annotations = models.JSONField(blank=True)
    history = models.JSONField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Annotation({self.file.resource.name} | {self.file.version} | {self.owner} | {self.id})"


class Clip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name='clips')
    start_time = models.FloatField()
    end_time = models.FloatField()
    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    tags = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Clip({self.content.title} | {self.start_time}-{self.end_time} | {self.id})"


class Content(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255, blank=True)
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name='contents', null=True, blank=True)
    url = models.URLField(max_length=500, blank=True, null=True)
    description = models.TextField(blank=True)
    tags = models.TextField(blank=True)
    allow_definitions = models.BooleanField(default=True)
    allow_notes = models.BooleanField(default=True)
    allow_captions = models.BooleanField(default=True)
    views = models.IntegerField(default=0)
    published = models.BooleanField(default=False)
    words = models.TextField(blank=True)
    annotation = models.ForeignKey(Annotation, on_delete=models.CASCADE, related_name='contents', null=True, blank=True)
    clips = models.ManyToManyField(Clip, related_name='contents', blank=True)
    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name='contents', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'content'

    def __str__(self):
        return self.title or f"Content {self.id}"


class Course(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dept = models.CharField(max_length=5)
    catalog_number = models.CharField(max_length=4)
    section_number = models.CharField(max_length=3)

    class Meta:
        unique_together = ('dept', 'catalog_number', 'section_number')

    def __str__(self):
        return f"Course({self.dept} {self.catalog_number}-{self.section_number} | {self.id})"


class Subtitle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name='subtitles')
    name = models.CharField(max_length=255)
    language = models.CharField(max_length=10)
    subtitles = models.JSONField(blank=True)
    words = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Subtitle({self.content.title} | {self.language} | {self.id})"


class Language(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    language = models.CharField(max_length=30)

    def __str__(self):
        return f"Lang({self.language})"


class AuthToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='auth_tokens')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"AuthToken({self.user.netid} | {self.id})"


class FileKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='file_keys')
    file = models.ForeignKey(File, on_delete=models.CASCADE, related_name='file_keys')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"FileKey({self.user.name} | {self.file.resource.name} | {self.id})"


class Email(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='emails')
    sender_email = models.EmailField(max_length=255)
    recipients = models.JSONField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Email({self.user.netid} | {self.subject} | {self.id})"
