from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from ledger.errors import LedgerError
from ledger.reconciliation.service import repair_snapshot


class Command(BaseCommand):
    help = (
        "Set the snapshot of an open Incident's account to the recount of Entries. "
        "Manual only (ADR-0003); close_incident afterwards returns trust."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("incident_id", type=int)
        parser.add_argument("--note", required=True, help="why the snapshot is being rewritten")
        parser.add_argument("--actor", required=True, help="who runs the repair (name or login)")

    def handle(self, *_args: Any, **options: Any) -> None:
        try:
            repair = repair_snapshot(
                options["incident_id"], note=options["note"], actor=options["actor"]
            )
        except LedgerError as error:
            raise CommandError(f"{error.code}: {error.detail}") from error
        self.stdout.write(
            f"Incident {repair.incident_id}: snapshot of account {repair.incident.account_id} "
            f"{repair.repaired_from} -> {repair.repaired_to} by {repair.actor}; still untrusted, "
            "run close_incident"
        )
