import logging

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.api import Api
from core.models import YearTerm

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Refresh the cached BYU term calendar. Course-derived playlist access "
        "depends on it, so this should run on a daily schedule."
    )

    def handle(self, *args, **options):
        try:
            year_terms = Api().get_year_terms()
        except Exception as e:
            raise CommandError(f"Failed to fetch yearterms from the BYU API: {e}")

        if not year_terms:
            raise CommandError(
                "The BYU API returned no usable yearterms; leaving the cache alone."
            )

        created_count = 0
        updated_count = 0
        for entry in year_terms:
            _, created = YearTerm.objects.update_or_create(
                yearterm=entry["yearterm"],
                defaults={
                    "start_date_time": entry["start_date_time"],
                    "end_date_time": entry["end_date_time"],
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Cached {created_count} new and {updated_count} existing yearterms."
            )
        )
