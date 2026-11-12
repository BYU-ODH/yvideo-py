"""Test helpers for International Cinema (outside tests/ so the test-naming hook ignores it)."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import modify_settings
from django.test import override_settings
from django.utils import timezone

from core.factories import AnnotationSetFactory
from core.factories import BlankAnnotationFactory
from core.factories import BlurAnchorPointFactory
from core.factories import BlurAnnotationFactory
from core.factories import ClipFactory
from core.factories import CommentAnnotationFactory
from core.factories import ContentFactory
from core.factories import MuteAnnotationFactory
from core.factories import PlaylistFactory
from core.factories import ResourceFactory
from core.factories import SubtitleFactory
from core.factories import TrackFactory
from core.factories import UserFactory
from core.models import IC_EDITOR_GROUP_NAME
from core.models import ResourceAccess
from core.models import ResourceFile
from core.models import SkipAnnotation
from core.models import projection_file_name

from .models import ICProgram
from .models import ICProgramItem
from .sync.bundle import file_xxh64
from .sync.notify import reset_pushable_cache

FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_mp4(path, seconds=2, faststart=False, size="320x240", rate=24):
    """A tiny real H.264/AAC MP4. Without +faststart, ffmpeg writes moov at the end."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={seconds}:size={size}:rate={rate}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={seconds}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
    ]
    if faststart:
        command += ["-movflags", "+faststart"]
    subprocess.run([*command, str(path)], check=True)
    return path


class ICTempDirsMixin:
    """Each test gets its own MEDIA_ROOT, IC_PROJECTION_ROOT and IC_STATE_DIR."""

    def setUp(self):
        super().setUp()
        self._ic_tmp = Path(tempfile.mkdtemp(prefix="ic-test-"))
        self.media_root = self._ic_tmp / "media"
        self.projection_root = self._ic_tmp / "projection"
        self.state_dir = self._ic_tmp / "state"
        for directory in (self.media_root, self.projection_root, self.state_dir):
            directory.mkdir()
        self._ic_settings = override_settings(
            MEDIA_ROOT=self.media_root,
            IC_PROJECTION_ROOT=self.projection_root,
            IC_STATE_DIR=self.state_dir,
        )
        self._ic_settings.enable()
        # force_login sessions carry no OIDC token expiry, which SessionRefresh redirects.
        self._ic_middleware = modify_settings(
            MIDDLEWARE={"remove": ["mozilla_django_oidc.middleware.SessionRefresh"]}
        )
        self._ic_middleware.enable()
        reset_pushable_cache()
        cache.clear()  # the manifest hash is cached between requests
        self.addCleanup(self._ic_cleanup)

    def _ic_cleanup(self):
        self._ic_middleware.disable()
        self._ic_settings.disable()
        shutil.rmtree(self._ic_tmp, ignore_errors=True)
        reset_pushable_cache()


def make_ic_editor(**kwargs):
    user = UserFactory(instructor=True, **kwargs)
    user.groups.add(Group.objects.get_or_create(name=IC_EDITOR_GROUP_NAME)[0])
    return user


def make_master(resource, payload=None, **fields):
    """A projection master with real bytes on disk (not a playable video)."""
    payload = payload or f"master bytes for {resource.pk} ".encode() * 1000
    checksum_holder = ResourceFile(resource=resource)
    handle, temp_name = tempfile.mkstemp()
    os.close(handle)
    temp = Path(temp_name)
    temp.write_bytes(payload)
    checksum = file_xxh64(temp)
    name = projection_file_name(resource.pk, checksum)
    destination = Path(checksum_holder.file.storage.path(name))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(temp, destination)
    defaults = {
        "version": f"ic-master-{checksum[:8]}",
        "video_codec": "h264",
        "audio_codec": "aac",
        "width": 1920,
        "height": 1080,
        "frame_rate": 24.0,
        "duration_seconds": 100.0,
        "bitrate": 10_000_000,
        "faststart": True,
        "probed_at": timezone.now(),
    }
    defaults.update(fields)
    master = ResourceFile(
        resource=resource,
        is_projection_file=True,
        checksum=checksum,
        checksum_at=timezone.now(),
        size_bytes=len(payload),
        **defaults,
    )
    master.file.name = name
    master.save()
    return master


def make_sd_file(master):
    payload = f"sd bytes for {master.pk}".encode()
    sd = ResourceFile(
        resource=master.resource,
        version=f"ic-sd-{master.checksum[:8]}",
        derived_from=master,
        width=854,
        height=480,
        probed_at=timezone.now(),
        faststart=True,
        size_bytes=len(payload),
    )
    relative = f"{master.resource.name}/ic-sd-{master.checksum[:8]}.mp4"
    path = Path(sd.file.storage.path(relative))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    sd.file.name = relative
    sd.save()
    return sd


def make_film(editor, playlist, title, *, with_annotations=True, with_subtitles=True):
    """A content in an IC playlist with a master, its SD file, annotations and VTT."""
    resource = ResourceFactory(name=f"{title} resource")
    for user in {editor, playlist.owner}:
        ResourceAccess.objects.get_or_create(user=user, resource=resource)
    master = make_master(resource)
    sd = make_sd_file(master)
    annotation_set = None
    if with_annotations:
        annotation_set = AnnotationSetFactory(resource=resource, owner=playlist.owner)
        track = TrackFactory(annotation_set=annotation_set, name="Track 1")
        second = TrackFactory(
            annotation_set=annotation_set, name="Track 2", stack_position=1
        )
        skip = SkipAnnotation.objects.create(
            track=track, name="Logo", start_time=1, end_time=3
        )
        # Edit history: an inactive version linked by prev/next.
        skip.edit(end_time=4)
        MuteAnnotationFactory(track=track, start_time=5, end_time=7)
        BlankAnnotationFactory(track=second, start_time=8, end_time=9, type="k")
        blur = BlurAnnotationFactory(track=second, start_time=10, end_time=14)
        BlurAnchorPointFactory(
            blur_annotation=blur, time=10, x=10, y=10, width=20, height=20
        )
        BlurAnchorPointFactory(
            blur_annotation=blur, time=12.5, x=30, y=15, width=20, height=20
        )
        CommentAnnotationFactory(track=track, start_time=15, end_time=16)
        ClipFactory(track=track, start_time=0, end_time=20)
    content = ContentFactory(
        title=title,
        playlist=playlist,
        resource_file=sd,
        annotation_set=annotation_set,
        grant_owner_resource_access=False,
    )
    if with_subtitles:
        subtitle = SubtitleFactory(
            resource=resource, owner=playlist.owner, name=f"{title} English"
        )
        content.default_subtitle_track = subtitle
        content.save()
    return content


def make_program_scenario(on=None):
    """An active program: a looping announcement and a feature with full annotations."""
    on = on or timezone.localdate()
    editor = make_ic_editor()
    playlist = PlaylistFactory(owner=editor, is_ic=True, name="IC Fall")
    announcement = make_film(
        editor,
        playlist,
        "Pre-show announcements",
        with_annotations=False,
        with_subtitles=False,
    )
    feature = make_film(editor, playlist, "Amélie")
    program = ICProgram.objects.create(name="Amélie week", start_date=on, end_date=on)
    loop = ICProgramItem.objects.create(
        program=program,
        content=announcement,
        position=0,
        mode=ICProgramItem.Mode.LOOP,
        subtitles=ICProgramItem.Subtitles.NONE,
    )
    film = ICProgramItem.objects.create(
        program=program,
        content=feature,
        position=1,
        mode=ICProgramItem.Mode.ONCE,
        subtitles=ICProgramItem.Subtitles.VTT,
    )
    return {
        "editor": editor,
        "playlist": playlist,
        "program": program,
        "loop_item": loop,
        "film_item": film,
        "announcement": announcement,
        "feature": feature,
    }
