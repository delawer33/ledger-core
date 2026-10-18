import logging
import time
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db import Error as DatabaseError
from django.db import close_old_connections, connection

from ledger.scheduler import ensure_scheduled

logger = logging.getLogger(__name__)

BACKOFF_START_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0


class Command(BaseCommand):
    help = (
        "Keep the periodic tasks (hold expiry, reconciliation, outbox relay) alive: every "
        "SCHEDULER_POLL_SECONDS start any chain that has none. Safe to run in several copies. "
        "Survives database restarts: a failed pass is logged and retried with backoff. "
        "Run db_worker to execute the tasks."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--once", action="store_true", help="ensure the chains once and exit")
        parser.add_argument(
            "--heartbeat-file",
            type=Path,
            help="touch this file after every successful pass (for a container healthcheck)",
        )

    def handle(self, *_args: Any, **options: Any) -> None:
        heartbeat: Path | None = options["heartbeat_file"]
        backoff = BACKOFF_START_SECONDS
        while True:
            close_old_connections()
            try:
                started = [name for name, new in ensure_scheduled().items() if new]
            except DatabaseError:
                if options["once"]:
                    raise
                logger.exception("Scheduler pass failed, retrying in %.0f s", backoff)
                connection.close()
                time.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)
                continue
            backoff = BACKOFF_START_SECONDS
            if started:
                self.stdout.write("Started: " + ", ".join(started))
            if heartbeat is not None:
                heartbeat.touch()
            if options["once"]:
                return
            time.sleep(settings.SCHEDULER_POLL_SECONDS)
