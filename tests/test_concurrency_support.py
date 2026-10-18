"""The LEDGER_CONCURRENCY_* parsing: bounds are enforced, but only the concurrency suite fails."""

import importlib

import pytest

from . import concurrency_support
from .concurrency_support import ConcurrencyConfigError, _env_int


def _read(raw: str | None, *, minimum: int = 2) -> int:
    environ = {} if raw is None else {"X": raw}
    return _env_int("X", 8, minimum=minimum, reason="r", environ=environ)


def test_unset_variable_gives_the_default() -> None:
    assert _read(None) == 8


@pytest.mark.parametrize("raw", ["2", "3", "100"])
def test_value_at_or_above_minimum_is_accepted(raw: str) -> None:
    assert _read(raw) == int(raw)


@pytest.mark.parametrize("raw", ["1", "0", "-5"])
def test_value_below_minimum_is_rejected_with_the_name_and_the_bound(raw: str) -> None:
    with pytest.raises(ConcurrencyConfigError, match=r"X=.*at least 2"):
        _read(raw)


@pytest.mark.parametrize("raw", ["", "many", "2.5"])
def test_non_integer_is_rejected(raw: str) -> None:
    with pytest.raises(ConcurrencyConfigError, match="not an integer"):
        _read(raw)


def test_bad_variable_does_not_break_import_but_fails_check_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEDGER_CONCURRENCY_THREADS", "1")
    try:
        reloaded = importlib.reload(concurrency_support)  # importing must not raise
        with pytest.raises(reloaded.ConcurrencyConfigError, match="LEDGER_CONCURRENCY_THREADS=1"):
            reloaded.check_config()
    finally:
        monkeypatch.undo()
        importlib.reload(concurrency_support)
