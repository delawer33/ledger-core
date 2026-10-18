"""The three scenarios. Each is a function of one worker step; the runner loops it until the deadline."""

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from ledger.loadgen.client import ApiClient, Reply

MAX_TRANSFER = 100
HOLD_TTL_SECONDS = 3600
# Mixed scenario weights: transfer, hold + capture, hold + release, replay of an earlier transfer.
MIXED_WEIGHTS = (50, 20, 15, 15)


@dataclass
class WorkerContext:
    """What one worker needs; never shared between threads."""

    client: ApiClient
    accounts: list[int]
    rng: random.Random
    index: int
    run_id: str
    counter: int = 0
    sent: list[tuple[str, dict[str, object], int]] = field(default_factory=list)

    def next_key(self) -> str:
        self.counter += 1
        return f"lg-{self.run_id}-{self.index}-{self.counter}"


Step = Callable[[WorkerContext], list[Reply]]


def _transfer_body(source: int, target: int, amount: int) -> dict[str, object]:
    return {
        "kind": "transfer",
        "entries": [
            {"account_id": source, "amount": -amount},
            {"account_id": target, "amount": amount},
        ],
    }


def _transfer(ctx: WorkerContext, source: int, target: int) -> Reply:
    body = _transfer_body(source, target, ctx.rng.randint(1, MAX_TRANSFER))
    key = ctx.next_key()
    reply = ctx.client.post("transfer", "/transactions", body, key)
    if reply.ok:
        ctx.sent.append((key, body, reply.status))
    return reply


def random_step(ctx: WorkerContext) -> list[Reply]:
    """Transfer between two random distinct accounts."""
    source, target = ctx.rng.sample(ctx.accounts, 2)
    return [_transfer(ctx, source, target)]


def hot_step(ctx: WorkerContext) -> list[Reply]:
    """Opposite transfers between two accounts: half the workers a->b, half b->a."""
    first, second = ctx.accounts[0], ctx.accounts[1]
    source, target = (first, second) if ctx.index % 2 == 0 else (second, first)
    return [_transfer(ctx, source, target)]


def mixed_step(ctx: WorkerContext) -> list[Reply]:
    choice = ctx.rng.choices(range(len(MIXED_WEIGHTS)), weights=MIXED_WEIGHTS)[0]
    if choice == 3 and ctx.sent:
        return [_replay(ctx)]
    if choice in (1, 2):
        return _hold_cycle(ctx, capture=choice == 1)
    return random_step(ctx)


def _replay(ctx: WorkerContext) -> Reply:
    """Resend an earlier transfer with its Idempotency-Key; the stored reply must come back."""
    key, body, original_status = ctx.rng.choice(ctx.sent)
    reply = ctx.client.post("replay", "/transactions", body, key)
    if reply.ok and reply.status != original_status:
        return Reply("replay", reply.status, "replay_mismatch", reply.latency_ms, reply.body)
    return reply


def _hold_cycle(ctx: WorkerContext, *, capture: bool) -> list[Reply]:
    source, target = ctx.rng.sample(ctx.accounts, 2)
    amount = ctx.rng.randint(1, MAX_TRANSFER)
    placed = ctx.client.post(
        "hold",
        "/holds",
        {"account_id": source, "amount": amount, "ttl_seconds": HOLD_TTL_SECONDS},
        ctx.next_key(),
    )
    if not placed.ok:
        return [placed]
    hold_id = placed.body["id"]
    if capture:
        closing = ctx.client.post(
            "capture", f"/holds/{hold_id}/capture", {"to_account_id": target}, ctx.next_key()
        )
    else:
        closing = ctx.client.post("release", f"/holds/{hold_id}/release", {}, ctx.next_key())
    return [placed, closing]


SCENARIOS: dict[str, Step] = {"random": random_step, "hot": hot_step, "mixed": mixed_step}
