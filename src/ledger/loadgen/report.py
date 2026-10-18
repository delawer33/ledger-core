"""Report assembly: statistics, hardware, version, JSON and markdown rendering, table collection."""

import json
import math
import os
import platform
import subprocess
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from ledger.loadgen.client import Reply

OK = "OK"
FAILED = "FAILED"


class ReportRejectedError(ValueError):
    """A report cannot go into the README table."""


def percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile of an already sorted sequence; 0 for no data."""
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


def summarize(replies: Iterable[Reply], elapsed: float) -> dict[str, Any]:
    """ops/s counts successful (2xx) requests; latencies cover every request, errors included."""
    all_replies = list(replies)
    latencies = sorted(r.latency_ms for r in all_replies)
    ok = sum(1 for r in all_replies if r.ok)
    by_status = Counter(str(r.status) for r in all_replies if not r.ok)
    by_code = Counter(r.code or f"http_{r.status}" for r in all_replies if not r.ok)
    return {
        "requests": len(all_replies),
        "ok": ok,
        "elapsed_s": round(elapsed, 3),
        "ops_per_s": round(ok / elapsed, 2) if elapsed > 0 else 0.0,
        "p50_ms": round(percentile(latencies, 0.50), 2),
        "p99_ms": round(percentile(latencies, 0.99), 2),
        "errors_by_http": dict(sorted(by_status.items())),
        "errors_by_code": dict(sorted(by_code.items())),
    }


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _ram_gib() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024 / 1024, 1)
    except (OSError, ValueError):
        pass
    return None


def hardware(container_limits: str | None) -> dict[str, Any]:
    """Machine of the generator host plus the container limits as stated by the operator."""
    return {
        "cpu_model": _cpu_model(),
        "cpu_cores": os.cpu_count(),
        "ram_gib": _ram_gib(),
        "container_limits": container_limits or os.environ.get("LOADGEN_CONTAINER_LIMITS"),
        "os": platform.platform(),
    }


def git_sha() -> str:
    explicit = os.environ.get("LOADGEN_GIT_SHA")
    if explicit:
        return explicit
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return done.stdout.strip() or "unknown"


def _fmt_map(values: dict[str, int]) -> str:
    return ", ".join(f"{k}: {v}" for k, v in values.items()) or "-"


def render_markdown(reports: Sequence[dict[str, Any]]) -> str:
    header = (
        "| scenario | workers | accounts | duration, s | ops/s | p50, ms | p99, ms "
        "| errors (HTTP) | errors (rule) | deadlocks | invariants | git |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    rows = []
    for report in reports:
        params, result = report["params"], report["results"]
        rows.append(
            f"| {report['scenario']} | {params['workers']} | {params['accounts']} "
            f"| {result['elapsed_s']} | {result['ops_per_s']} | {result['p50_ms']} "
            f"| {result['p99_ms']} | {_fmt_map(result['errors_by_http'])} "
            f"| {_fmt_map(result['errors_by_code'])} | {report['deadlocks']} "
            f"| {report['status']} | {report['version']} |"
        )
    return header + "\n".join(rows) + "\n"


def write_json(report: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def collect_table(paths: Sequence[Path]) -> str:
    """Markdown table from saved reports; any report that is not a passed run is rejected."""
    reports: list[dict[str, Any]] = []
    for path in paths:
        report = json.loads(path.read_text())
        invariants = report.get("invariants") or {}
        if report.get("status") != OK or not invariants.get("ok"):
            raise ReportRejectedError(f"{path}: invariants did not pass, report is FAILED")
        reports.append(report)
    return render_markdown(reports)
