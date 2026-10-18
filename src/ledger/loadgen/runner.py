"""Setup, the worker threads, the deadlock counter and the final invariant check."""

import random
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import connection

from ledger.accounts.models import Account
from ledger.accounts.system import seed_system_accounts
from ledger.dbidentity import database_identity
from ledger.invariants import check_invariants
from ledger.loadgen import report as reporting
from ledger.loadgen.client import ApiClient, Reply
from ledger.loadgen.scenarios import SCENARIOS, WorkerContext

CURRENCY = "USD"
FUND_AMOUNT = 1_000_000_000
# pg_stat_database is flushed by backends about once a second; wait it out before the second read.
STATS_FLUSH_SECONDS = 1.5
OPENAPI_PATH = "/openapi.json"


class LoadgenError(RuntimeError):
    """The run could not start: the service is unreachable or setup was refused."""


@dataclass(frozen=True)
class RunParams:
    base_url: str
    scenario: str
    workers: int
    duration: float
    accounts: int
    seed: int
    container_limits: str | None = None


def deadlock_count() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")
        row = cursor.fetchone()
    return int(row[0]) if row else 0


def _check_same_database(params: RunParams, health_body: dict[str, Any]) -> None:
    """Refuse to run when DATABASE_URL is not the database behind --base-url."""
    served = health_body.get("database_id")
    if not served:
        raise LoadgenError(
            f"service at {params.base_url} does not report database_id in /health; "
            "cannot confirm it uses the same database as DATABASE_URL"
        )
    own = database_identity()
    if served != own:
        raise LoadgenError(
            f"DATABASE_URL points to another database than the service at {params.base_url} "
            f"(service {served}, generator {own}); invariants would be checked on the wrong data"
        )


def detect_features(client: ApiClient) -> dict[str, Any]:
    """Which optional operations the service offers, read from its OpenAPI document.

    Fee and payout (ticket 26) may or may not be merged; the scenarios exercise only the
    operations they know the shape of, and the report states what the service advertised.
    """
    reply = client.get("openapi", OPENAPI_PATH)
    paths = sorted(reply.body.get("paths", {})) if reply.ok else []
    return {"fee_payout_paths": [p for p in paths if "fee" in p or "payout" in p]}


def _setup_accounts(client: ApiClient, params: RunParams, run_id: str) -> list[int]:
    """Create the accounts over HTTP and fund them from system:clearing."""
    seed_system_accounts([CURRENCY])
    clearing = Account.objects.get(owner="system:clearing", currency=CURRENCY, is_system=True)
    ids: list[int] = []
    for i in range(params.accounts):
        created = client.post(
            "setup_account",
            "/accounts",
            {"currency": CURRENCY, "type": "liability", "owner": f"loadgen:{run_id}:{i}"},
            f"lg-{run_id}-acc-{i}",
        )
        if not created.ok:
            raise LoadgenError(f"cannot create account: HTTP {created.status} {created.code}")
        account_id = int(created.body["id"])
        funded = client.post(
            "setup_fund",
            "/transactions",
            {
                "kind": "transfer",
                "entries": [
                    {"account_id": clearing.pk, "amount": -FUND_AMOUNT},
                    {"account_id": account_id, "amount": FUND_AMOUNT},
                ],
            },
            f"lg-{run_id}-fund-{i}",
        )
        if not funded.ok:
            raise LoadgenError(f"cannot fund account: HTTP {funded.status} {funded.code}")
        ids.append(account_id)
    return ids


def _work(
    params: RunParams, run_id: str, accounts: list[int], index: int, deadline_at: list[float]
) -> list[Reply]:
    step = SCENARIOS[params.scenario]
    client = ApiClient(params.base_url)
    ctx = WorkerContext(
        client=client,
        accounts=accounts,
        rng=random.Random(params.seed * 10_000 + index),
        index=index,
        run_id=run_id,
    )
    replies: list[Reply] = []
    try:
        while time.monotonic() < deadline_at[0]:
            replies.extend(step(ctx))
    finally:
        client.close()
    return replies


def run_load(params: RunParams) -> dict[str, Any]:
    if params.scenario not in SCENARIOS:
        raise LoadgenError(f"unknown scenario {params.scenario!r}")
    if params.accounts < 2 or params.workers < 1 or params.duration <= 0:
        raise LoadgenError("need accounts >= 2, workers >= 1 and duration > 0")
    run_id = uuid.uuid4().hex[:8]
    setup_client = ApiClient(params.base_url)
    try:
        health = setup_client.get("health", "/health")
        if not health.ok:
            raise LoadgenError(f"service at {params.base_url} is not healthy: {health.status}")
        _check_same_database(params, health.body)
        features = detect_features(setup_client)
        accounts = _setup_accounts(setup_client, params, run_id)
    finally:
        setup_client.close()

    deadlocks_before = deadlock_count()
    results: list[list[Reply]] = [[] for _ in range(params.workers)]
    errors: list[BaseException] = []
    start = threading.Barrier(params.workers + 1)
    deadline_at = [0.0]

    def target(index: int) -> None:
        try:
            start.wait()
            results[index] = _work(params, run_id, accounts, index, deadline_at)
        except BaseException as exc:  # noqa: BLE001 - surfaced after the join, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=target, args=(i,)) for i in range(params.workers)]
    for thread in threads:
        thread.start()
    started = time.monotonic()
    deadline_at[0] = started + params.duration
    start.wait()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started
    if errors:
        raise LoadgenError(f"worker crashed: {errors[0]!r}") from errors[0]

    time.sleep(STATS_FLUSH_SECONDS)
    deadlocks = deadlock_count() - deadlocks_before
    summary = reporting.summarize([r for chunk in results for r in chunk], elapsed)
    invariants = check_invariants()
    return {
        "status": reporting.OK
        if invariants.ok and not summary["replay_mismatches"]
        else reporting.FAILED,
        "scenario": params.scenario,
        "version": reporting.git_sha(),
        "params": {
            "base_url": params.base_url,
            "workers": params.workers,
            "duration_s": params.duration,
            "accounts": params.accounts,
            "seed": params.seed,
        },
        "hardware": reporting.hardware(params.container_limits),
        "features": features,
        "results": summary,
        "deadlocks": deadlocks,
        "invariants": {
            "ok": invariants.ok,
            "accounts_checked": invariants.accounts_checked,
            "posted_total": invariants.posted_total,
            "violations": invariants.violations,
        },
    }
