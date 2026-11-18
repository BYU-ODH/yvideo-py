from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.demo_films import DEMO_FILMS
from core.demo_films import fetch_film


class Command(BaseCommand):
    help = (
        "Download the public-domain demo films (~2 GB) from archive.org into "
        "DEMO_FILMS_DIR. Run seed_demo_data afterwards to add them."
    )

    def handle(self, *args, **options):
        self.stdout.write(f"Cache: {settings.DEMO_FILMS_DIR}")
        for film in DEMO_FILMS:
            self.stdout.write(f"{film.title}: ", ending="")
            self.stdout.flush()
            try:
                downloaded = fetch_film(film)
            except (OSError, ValueError) as error:
                raise CommandError(f"Could not fetch {film.title}: {error}") from error
            self.stdout.write("downloaded" if downloaded else "already cached")
        self.stdout.write(self.style.SUCCESS("Demo films ready."))
