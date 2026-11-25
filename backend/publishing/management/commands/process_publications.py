from django.core.management.base import BaseCommand, CommandError
from filelock import Timeout

from publishing.worker import run


class Command(BaseCommand):
    help = "Send approved and scheduled clips to their publishing destinations."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        try:
            run(once=options["once"])
        except Timeout as exc:
            raise CommandError("A publisher already owns this workspace storage.") from exc
        except KeyboardInterrupt:
            return
