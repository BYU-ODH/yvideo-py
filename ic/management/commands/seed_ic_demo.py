"""Seed a local International Cinema demo from the checked-in demo videos.

Run after seed_demo_data. It makes an IC playlist owned by the demo admin, uses
demo_media/color_grid.mp4 as a looping announcement and demo_media/birds.mp4 as the
feature (each as both its projection master and its SD file), copies the demo admin's
birds annotations, and schedules a program for today. Re-running replaces it.
"""

from datetime import timedelta
from pathlib import Path
import shutil
import struct

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from core.models import AnnotationSet
from core.models import Content
from core.models import Playlist
from core.models import Resource
from core.models import ResourceAccess
from core.models import ResourceFile
from core.models import User
from core.models import projection_file_name
from ic.models import ICProgram
from ic.models import ICProgramItem
from ic.sync.bundle import file_xxh64

PLAYLIST_NAME = "International Cinema (demo)"
PROGRAM_NAME = "IC demo program"
FILMS = [
    ("IC demo announcement", "color_grid.mp4", ICProgramItem.Mode.LOOP),
    ("IC demo feature", "birds.mp4", ICProgramItem.Mode.ONCE),
]


class Command(BaseCommand):
    help = "Seed an International Cinema playlist and program from demo_media (development only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true", help="Seed even though DEBUG is False."
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to seed demo data while DEBUG is False. Pass --force if you "
                "intentionally want to do that."
            )
        admin = User.objects.filter(is_superuser=True, username="devadmin").first()
        if admin is None:
            raise CommandError("Run `manage.py seed_demo_data` first.")
        demo_media = Path(settings.BASE_DIR) / "demo_media"
        with transaction.atomic():
            ICProgram.objects.filter(name=PROGRAM_NAME).delete()
            Playlist.objects.filter(name=PLAYLIST_NAME, owner=admin).delete()
            for title, _, _ in FILMS:
                Resource.objects.filter(name=title).delete()

            playlist = Playlist.objects.create(
                name=PLAYLIST_NAME, owner=admin, is_ic=True, published=True
            )
            today = timezone.localdate()
            program = ICProgram.objects.create(
                name=PROGRAM_NAME, start_date=today, end_date=today + timedelta(days=7)
            )
            birds_set = AnnotationSet.objects.filter(
                resource__name__icontains="birds"
            ).first()
            for position, (title, filename, mode) in enumerate(FILMS):
                content = self._film(admin, playlist, title, demo_media / filename)
                if mode == ICProgramItem.Mode.ONCE and birds_set is not None:
                    copy = AnnotationSet.create_for_content(
                        content,
                        admin,
                        set_name="IC demo annotations",
                        annotation_set_id_to_copy=birds_set.pk,
                    )
                    content.annotation_set = copy
                    content.save()
                ICProgramItem.objects.create(
                    program=program,
                    content=content,
                    position=position,
                    mode=mode,
                    subtitles=ICProgramItem.Subtitles.NONE,
                )
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {PROGRAM_NAME}: /ic/manage/programs/{program.pk}/"
            )
        )

    def _film(self, admin, playlist, title, source):
        resource = Resource.objects.create(name=title, requester_username="devadmin")
        ResourceAccess.objects.create(user=admin, resource=resource)

        # seed_demo_data already registered these videos, and checksums are unique, so
        # each copy gets a trailing MP4 "free" box (ignored by players) to make its bytes,
        # and therefore its checksum, its own.
        master = ResourceFile(
            resource=resource,
            is_projection_file=True,
            version="ic-master-demo",
            video_codec="h264",
            faststart=True,
            probed_at=timezone.now(),
        )
        staged = Path(settings.MEDIA_ROOT) / ".ic-demo-master.mp4"
        _copy_with_free_box(source, staged, b"ic-demo-master")
        checksum = file_xxh64(staged)
        name = projection_file_name(resource.pk, checksum)
        destination = Path(master.file.storage.path(name))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(staged, destination)
        master.file.name = name
        master.checksum = checksum
        master.checksum_at = timezone.now()
        master.size_bytes = destination.stat().st_size
        master.save()

        # No real transcode in the demo: the SD file is the same video, linked as derived
        # from the master so readiness sees the editing file on the master's timeline.
        sd_name = f"{resource.name}/ic-sd-demo.mp4"
        sd_path = Path(settings.MEDIA_ROOT) / sd_name
        sd_path.parent.mkdir(parents=True, exist_ok=True)
        _copy_with_free_box(source, sd_path, b"ic-demo-sd")
        sd = ResourceFile(
            resource=resource,
            version="ic-sd-demo",
            derived_from=master,
            checksum=file_xxh64(sd_path),
            checksum_at=timezone.now(),
            size_bytes=sd_path.stat().st_size,
            faststart=True,
            probed_at=timezone.now(),
        )
        sd.file.name = sd_name
        sd.save()
        return Content.objects.create(
            title=title, playlist=playlist, resource_file=sd, published=True
        )


def _copy_with_free_box(source, destination, payload):
    shutil.copyfile(source, destination)
    with open(destination, "ab") as handle:
        handle.write(struct.pack(">I4s", 8 + len(payload), b"free") + payload)
