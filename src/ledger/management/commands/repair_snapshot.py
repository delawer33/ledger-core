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

    def handle(self, *_args: Any, **options: Any) -> None:
        try:
            incident = repair_snapshot(options["incident_id"], note=options["note"])
        except LedgerError as error:
            raise CommandError(f"{error.code}: {error.detail}") from error
        self.stdout.write(
            f"Incident {incident.pk}: snapshot of account {incident.account_id} "
            f"{incident.repaired_from} -> {incident.repaired_to}; still untrusted, "
            "run close_incident"
        )
