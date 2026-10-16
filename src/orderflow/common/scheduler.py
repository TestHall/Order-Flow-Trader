"""Deterministischer Ereignistakt für Replay/Simulation.

Gleichzeitige Einträge werden nach Priorität und dann nach Einfügereihenfolge abgearbeitet:
Marktereignisse (0) vor Balkenabschlüssen (1) vor Order-/Regeltimern (2). Dadurch gewinnt bei
Gleichstand immer die bereits eingetroffene Marktinformation (konservativ, siehe docs/ANNAHMEN.md).
"""
from __future__ import annotations

import heapq
from typing import Callable

PRIO_MARKET = 0
PRIO_BAR = 1
PRIO_TIMER = 2


class Scheduler:
    def __init__(self) -> None:
        self._heap: list[tuple[int, int, int, Callable[[int], None]]] = []
        self._counter = 0
        self.now = 0

    def at(self, t: int, prio: int, fn: Callable[[int], None]) -> None:
        if t < self.now:
            raise ValueError(f"Rückwirkende Planung verboten: {t} < {self.now}")
        heapq.heappush(self._heap, (t, prio, self._counter, fn))
        self._counter += 1

    def run(self, until: int | None = None) -> None:
        while self._heap:
            t, _, _, fn = self._heap[0]
            if until is not None and t > until:
                return
            heapq.heappop(self._heap)
            self.now = t
            fn(t)

    def pending(self) -> int:
        return len(self._heap)
