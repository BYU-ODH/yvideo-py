from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from core.dev_seed import seed_demo_data


class Command(BaseCommand):
    help = "Create deterministic local demo data and copy sample media into MEDIA_ROOT."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow seeding when DEBUG is False.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to seed deterministic demo data while DEBUG is False. "
                "Pass --force if you intentionally want to do that."
            )

        summary = seed_demo_data()
        self.stdout.write(self.style.SUCCESS("Seeded deterministic demo data."))
        self.stdout.write(
            "\n".join(
                [
                    f"Users: {summary['users']}",
                    f"Resources: {summary['resources']}",
                    f"Collections: {summary['collections']}",
                    f"Contents: {summary['contents']}",
                    f"Admin netid: {summary['seeded_admin_netid']}",
                    f"Admin password: {summary['seeded_admin_password']}",
                    f"Sample content: {summary['sample_content_title']}",
                ]
            )
        )
