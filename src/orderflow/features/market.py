"""Sicht auf den zuletzt empfangenen Quote-Stand (nur bereits empfangene Informationen)."""
from __future__ import annotations

from dataclasses import dataclass

from orderflow.dataadapter.events import MarketEvent


@dataclass(frozen=True)
class Quote:
    bid_ticks: int
    ask_ticks: int
    bid_size: int
    ask_size: int
    exchange_ts: int
    receive_ts: int
    event_id: str

    @property
    def spread_ticks(self) -> int:
        return self.ask_ticks - self.bid_ticks


class MarketView:
    def __init__(self) -> None:
        self.quote: Quote | None = None
        self.last_trade_ticks: int | None = None

    def on_quote(self, e: MarketEvent) -> None:
        self.quote = Quote(e.bid_ticks, e.ask_ticks, e.bid_size, e.ask_size, e.exchange_ts, e.receive_ts, e.event_id)

    def on_trade(self, e: MarketEvent) -> None:
        self.last_trade_ticks = e.price_ticks

    def quote_age_us(self, now: int) -> int | None:
        return None if self.quote is None else now - self.quote.receive_ts
