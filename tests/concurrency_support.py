"""Helpers for the concurrency proofs: tunable load and a thread runner that surfaces failures."""

import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from django.db import connection


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# CI-friendly defaults; raise them locally to push harder.
THREADS = _env_int("LEDGER_CONCURRENCY_THREADS", 8)
OPS_PER_THREAD = _env_int("LEDGER_CONCURRENCY_OPS", 25)
ACCOUNTS = _env_int("LEDGER_CONCURRENCY_ACCOUNTS", 5)
OPPOSITE_ROUNDS = _env_int("LEDGER_CONCURRENCY_ROUNDS", 40)
REPEATERS = _env_int("LEDGER_CONCURRENCY_REPEATERS", 12)


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
