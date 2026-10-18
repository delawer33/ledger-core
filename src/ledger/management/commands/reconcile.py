from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from ledger.reconciliation.service import reconcile
from ledger.reconciliation.tasks import reconcile_task


class Command(BaseCommand):
    help = "Recount every Posted Balance from Entries and open Incidents on mismatch."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--enqueue",
            action="store_true",
            help="put the run on the task queue (for cron) instead of running it here",
        )

    def handle(self, *_args: Any, **options: Any) -> None:
        if options["enqueue"]:
            result = reconcile_task.enqueue()
            self.stdout.write(f"Reconciliation enqueued: {result.id}")
            return
        outcome = reconcile()
        self.stdout.write(
            f"Reconciliation: {outcome.accounts_checked} accounts, "
            f"{len(outcome.incidents_opened)} incidents opened, "
            f"{len(outcome.already_open)} already open, "
            f"{len(outcome.holds_exceeding_posted)} accounts with holds above Posted"
        )
