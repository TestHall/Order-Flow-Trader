"""Wissensstand: welche Ereignisse bis zu einem Empfangszeitpunkt verarbeitet wurden (Kap. 5.2)."""
from __future__ import annotations

from dataclasses import dataclass

from orderflow.common.hashing import GENESIS, chain
from orderflow.dataadapter.events import MarketEvent


@dataclass
class Knowledge:
    count: int = 0
    digest: str = GENESIS
    max_exchange_ts: int | None = None

    def add(self, e: MarketEvent) -> None:
        self.count += 1
        self.digest = chain(self.digest, e.event_hash)
        if self.max_exchange_ts is None or e.exchange_ts > self.max_exchange_ts:
            self.max_exchange_ts = e.exchange_ts
