from django.core.management.base import BaseCommand, CommandError
from filelock import Timeout

from studio.worker import run_queue


class Command(BaseCommand):
    help = "Process the studio render queue."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process at most one queued render.")

    def handle(self, *args, **options):
        try:
            run_queue(once=options["once"])
        except Timeout as exc:
            raise CommandError("A render worker already owns this data directory.") from exc
        except KeyboardInterrupt:
            return
