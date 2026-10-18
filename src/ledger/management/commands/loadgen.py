import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from ledger.loadgen import report as reporting
from ledger.loadgen.runner import LoadgenError, RunParams, run_load
from ledger.loadgen.scenarios import SCENARIOS


class Command(BaseCommand):
    help = (
        "Load generator: `loadgen run` against a running service, `loadgen table` for the README."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        sub = parser.add_subparsers(dest="action", required=True)
        run = sub.add_parser("run", help="run one scenario, print JSON and a markdown table")
        run.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
        run.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
        run.add_argument("--workers", type=int, default=8)
        run.add_argument("--duration", type=float, default=30.0, help="seconds")
        run.add_argument("--accounts", type=int, default=20)
        run.add_argument("--seed", type=int, default=1)
        run.add_argument("--output", type=Path, help="write the JSON report here")
        run.add_argument(
            "--container-limits",
            help="free text for the report, e.g. 'api 2 cpu/1g, db 2 cpu/2g' (or LOADGEN_CONTAINER_LIMITS)",
        )
        table = sub.add_parser("table", help="markdown table from saved JSON reports")
        table.add_argument("files", nargs="+", type=Path)

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        if options["action"] == "table":
            try:
                self.stdout.write(reporting.collect_table(options["files"]))
            except (reporting.ReportRejectedError, OSError, ValueError) as exc:
                raise CommandError(str(exc)) from exc
            return
        params = RunParams(
            base_url=options["base_url"],
            scenario=options["scenario"],
            workers=options["workers"],
            duration=options["duration"],
            accounts=options["accounts"],
            seed=options["seed"],
            container_limits=options["container_limits"],
        )
        try:
            report = run_load(params)
        except LoadgenError as exc:
            raise CommandError(str(exc)) from exc
        if options["output"]:
            reporting.write_json(report, options["output"])
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        self.stdout.write("")
        self.stdout.write(reporting.render_markdown([report]))
        if report["status"] != reporting.OK:
            raise CommandError(
                "invariants FAILED: " + "; ".join(report["invariants"]["violations"])
            )
