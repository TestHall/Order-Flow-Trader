"""Explizites FIFO-Testmodell für Limit-Orders (Kap. 8.2, T08).

Nur ein Testmodell: CME nutzt je Produkt verschiedene Matchingalgorithmen [S6]; FIFO ist keine
allgemeine Börsenregel. Eine Preisberührung beweist keinen Fill.
"""
from __future__ import annotations

from dataclasses import dataclass

MODEL_ID = "FIFO_TEST_MODEL"


@dataclass(frozen=True)
class FifoResult:
    model: str
    own_filled: int
    queue_ahead_remaining: int
    general_exchange_rule: bool = False


def fifo_limit_fill(queue_ahead: int, executed_at_level: int, cancels_ahead: int, own_quantity: int) -> FifoResult:
    for name, v in (("queue_ahead", queue_ahead), ("executed_at_level", executed_at_level),
                    ("cancels_ahead", cancels_ahead), ("own_quantity", own_quantity)):
        if v < 0:
            raise ValueError(f"{name} darf nicht negativ sein")
    ahead = max(0, queue_ahead - cancels_ahead)
    consumed_ahead = min(ahead, executed_at_level)
    left_for_us = executed_at_level - consumed_ahead
    return FifoResult(MODEL_ID, min(own_quantity, left_for_us), ahead - consumed_ahead)
