"""Helpers for the concurrency proofs: tunable load and a thread runner that surfaces failures."""

import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from django.db import connection


class ConcurrencyConfigError(ValueError):
    """A LEDGER_CONCURRENCY_* variable holds a value the suite cannot run with."""


def _env_int(name: str, default: int, *, minimum: int, reason: str) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConcurrencyConfigError(f"{name}={raw!r} is not an integer") from None
    if value < minimum:
        raise ConcurrencyConfigError(
            f"{name}={value} is too low: need at least {minimum} ({reason})"
        )
    return value


# CI-friendly defaults; raise them locally to push harder.
THREADS = _env_int(
    "LEDGER_CONCURRENCY_THREADS", 8, minimum=2, reason="contention needs two writers"
)
OPS_PER_THREAD = _env_int(
    "LEDGER_CONCURRENCY_OPS", 25, minimum=1, reason="each thread must do something"
)
ACCOUNTS = _env_int(
    "LEDGER_CONCURRENCY_ACCOUNTS", 5, minimum=2, reason="a transfer needs two accounts"
)
OPPOSITE_ROUNDS = _env_int(
    "LEDGER_CONCURRENCY_ROUNDS", 40, minimum=1, reason="at least one round per direction"
)
REPEATERS = _env_int(
    "LEDGER_CONCURRENCY_REPEATERS", 12, minimum=2, reason="a repeat needs two requests"
)


def run_threads[T](count: int, work: Callable[[int], T]) -> list[T]:
    """Run `work(index)` in `count` threads released together; re-raise the first failure.

    Each thread has its own database connection, closed when the thread is done.
    """
    start = threading.Barrier(count)

    def runner(index: int) -> T:
        try:
            start.wait()
            return work(index)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(runner, index) for index in range(count)]
        return [future.result() for future in futures]
