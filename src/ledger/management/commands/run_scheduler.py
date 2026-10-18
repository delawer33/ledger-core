import time
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from ledger.scheduler import ensure_scheduled


class Command(BaseCommand):
    help = (
        "Keep the periodic tasks (hold expiry, reconciliation, outbox relay) alive: every "
        "SCHEDULER_POLL_SECONDS start any chain that has none. Safe to run in several copies. "
        "Run db_worker to execute the tasks."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--once", action="store_true", help="ensure the chains once and exit")
        parser.add_argument(
            "--heartbeat-file",
            type=Path,
            help="touch this file after every pass (for a container healthcheck)",
        )

    def handle(self, *_args: Any, **options: Any) -> None:
        heartbeat: Path | None = options["heartbeat_file"]
        while True:
            started = [name for name, new in ensure_scheduled().items() if new]
            if started:
                self.stdout.write("Started: " + ", ".join(started))
            if heartbeat is not None:
                heartbeat.touch()
            if options["once"]:
                return
            time.sleep(settings.SCHEDULER_POLL_SECONDS)
