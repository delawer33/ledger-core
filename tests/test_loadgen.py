import json
import random
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from ledger.accounts.models import Account
from ledger.balances.models import BalanceSnapshot
from ledger.loadgen import report as reporting
from ledger.loadgen.client import Reply
from ledger.loadgen.runner import LoadgenError, RunParams, _check_same_database, run_load
from ledger.loadgen.scenarios import WorkerContext, _replay


def _run(live_server: Any, scenario: str) -> dict[str, Any]:
    params = RunParams(
        base_url=f"{live_server.url}/api/v1",
        scenario=scenario,
        workers=3,
        duration=1.0,
        accounts=4,
        seed=7,
        container_limits="test",
    )
    return run_load(params)


@pytest.mark.parametrize("scenario", ["random", "hot", "mixed"])
def test_smoke_scenario_against_live_server(live_server: Any, scenario: str) -> None:
    report = _run(live_server, scenario)

    assert report["status"] == "OK"
    assert report["invariants"]["ok"]
    assert report["results"]["ok"] > 0
    assert report["results"]["p99_ms"] >= report["results"]["p50_ms"] > 0
    assert report["deadlocks"] == 0
    assert report["params"]["workers"] == 3
    assert report["hardware"]["container_limits"] == "test"
    assert reporting.render_markdown([report]).count("\n") == 3


@pytest.mark.django_db(transaction=True)
def test_corrupted_snapshot_marks_the_report_failed(live_server: Any) -> None:
    # A run whose data is already broken must come out FAILED, whatever the workers did.
    ok_report = _run(live_server, "random")
    assert ok_report["status"] == "OK"
    victim = Account.objects.filter(owner__startswith="loadgen:").first()
    assert victim is not None
    BalanceSnapshot.objects.filter(account=victim).update(posted_amount=12345)

    failed = _run(live_server, "random")

    assert failed["status"] == "FAILED"
    assert not failed["invariants"]["ok"]
    assert any("snapshot" in v for v in failed["invariants"]["violations"])


@pytest.mark.django_db(transaction=True)
def test_command_run_writes_json_and_fails_on_broken_ledger(
    live_server: Any, tmp_path: Path
) -> None:
    out = tmp_path / "run.json"
    stdout = StringIO()
    args = ["loadgen", "run", "--scenario", "hot", "--base-url", f"{live_server.url}/api/v1"]
    call_command(
        *args,
        "--workers",
        "2",
        "--duration",
        "1",
        "--accounts",
        "3",
        "--output",
        str(out),
        stdout=stdout,
    )

    assert json.loads(out.read_text())["status"] == "OK"
    assert "| hot |" in stdout.getvalue()

    BalanceSnapshot.objects.filter(account__owner__startswith="loadgen:").update(posted_amount=1)
    with pytest.raises(CommandError, match="FAILED"):
        call_command(*args, "--workers", "2", "--duration", "1", "--output", str(out))
    assert json.loads(out.read_text())["status"] == "FAILED"


def test_unreachable_service_is_a_clear_error() -> None:
    params = RunParams("http://127.0.0.1:1/api/v1", "random", 1, 1.0, 2, 1)
    with pytest.raises(LoadgenError, match="not healthy"):
        run_load(params)


def _report(status: str, ok: bool) -> dict[str, Any]:
    results = reporting.summarize([Reply("transfer", 201, None, 5.0, {})], 1.0)
    return {
        "status": status,
        "scenario": "random",
        "version": "abc",
        "params": {"workers": 1, "accounts": 2},
        "results": results,
        "deadlocks": 0,
        "invariants": {"ok": ok},
    }


def test_table_collects_passed_reports_and_rejects_failed(tmp_path: Path) -> None:
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(_report("OK", True)))
    bad.write_text(json.dumps(_report("FAILED", False)))

    assert "| random |" in reporting.collect_table([good])
    with pytest.raises(reporting.ReportRejectedError):
        reporting.collect_table([good, bad])
    with pytest.raises(CommandError, match="FAILED"):
        call_command("loadgen", "table", str(good), str(bad))


def test_table_rejects_status_ok_with_failed_invariants(tmp_path: Path) -> None:
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(_report("OK", False)))

    with pytest.raises(reporting.ReportRejectedError):
        reporting.collect_table([forged])


def test_summary_statistics() -> None:
    replies = [Reply("t", 201, None, float(i), {}) for i in range(1, 101)]
    replies.append(Reply("t", 422, "insufficient_funds", 1.0, {}))

    summary = reporting.summarize(replies, 2.0)

    assert summary["ok"] == 100
    assert summary["ops_per_s"] == 50.0
    assert summary["p50_ms"] == 50.0
    assert summary["p99_ms"] == 99.0
    assert summary["errors_by_http"] == {"422": 1}
    assert summary["errors_by_code"] == {"insufficient_funds": 1}


class _ReplayingClient:
    """Stands in for ApiClient: answers every post with a fixed reply."""

    def __init__(self, reply: Reply) -> None:
        self.reply = reply

    def post(self, name: str, path: str, body: dict[str, Any], key: str) -> Reply:
        return self.reply


def _replay_ctx(client: Any) -> WorkerContext:
    ctx = WorkerContext(client=client, accounts=[1, 2], rng=random.Random(1), index=0, run_id="t")
    ctx.sent.append(("k", {"kind": "transfer"}, 201, b'{"id": 1}'))
    return ctx


def test_replay_with_other_body_and_same_status_is_a_mismatch() -> None:
    second_transaction = Reply("replay", 201, None, 1.0, {"id": 2}, b'{"id": 2}')

    reply = _replay(_replay_ctx(_ReplayingClient(second_transaction)))

    assert reply.code == "replay_mismatch"
    assert not reply.ok
    summary = reporting.summarize([reply], 1.0)
    assert summary["replay_mismatches"] == 1
    assert summary["ok"] == 0
    assert summary["errors_by_code"] == {"replay_mismatch": 1}


def test_replay_with_identical_bytes_passes() -> None:
    same = Reply("replay", 201, None, 1.0, {"id": 1}, b'{"id": 1}')

    assert _replay(_replay_ctx(_ReplayingClient(same))).code is None


def test_table_rejects_report_with_replay_mismatch(tmp_path: Path) -> None:
    report = _report("OK", True)
    report["results"] = reporting.summarize(
        [Reply("replay", 201, "replay_mismatch", 5.0, {}, b"x")], 1.0
    )
    path = tmp_path / "mismatch.json"
    path.write_text(json.dumps(report))

    with pytest.raises(reporting.ReportRejectedError, match="replay_mismatch"):
        reporting.collect_table([path])


def test_generator_pointed_at_another_database_refuses_to_start(
    live_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ledger.loadgen.runner.database_identity", lambda: "another-database")

    with pytest.raises(LoadgenError, match="another database"):
        _run(live_server, "random")


def test_service_without_database_id_is_refused() -> None:
    params = RunParams("http://x/api/v1", "random", 1, 1.0, 2, 1)
    with pytest.raises(LoadgenError, match="database_id"):
        _check_same_database(params, {"status": "ok"})
