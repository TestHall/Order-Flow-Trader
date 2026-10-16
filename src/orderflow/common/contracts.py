"""Schnittstelle zwischen Regelmodul und Risiko/Ausführung.

Das Regelmodul erzeugt Absichten (Intents); nur das Ausführungsmodul erzeugt Fills.
Ein Vorschlag ist noch keine Order und kein Fill (Kap. 5.3).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntryIntent:
    client_order_id: str
    decision_id: str
    candidate_number: int
    side: str                 # "SELL" in OF-A-1.0
    quantity: int
    approved_quantity: int
    stop_ticks: int
    target_multiple: int
    signal_time: int          # Bestätigungszeitpunkt (Empfangszeit)
    valid_until: int          # signal_time + Signalgültigkeit
    entry_cutoff: int         # kein Entry-Fill ab diesem Zeitpunkt (15:55 New York)


@dataclass(frozen=True)
class ExitIntent:
    client_order_id: str
    side: str                 # "BUY" zum Glattstellen eines Shorts
    quantity: int
    reason: str               # STOP | TARGET | TIME | RISK_OVERRUN
    trigger_time: int
    trigger_ref: str


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    side: str
    quantity: int
    price_ticks: int
    ts: int
    quote_event_id: str
    quote_bid_ticks: int
    quote_ask_ticks: int
    quote_age_us: int
    extra_slippage_ticks: int
    after_cancel_request: bool = False
    delayed_for_liquidity: bool = False

    def to_record(self) -> dict:
        return {
            "client_order_id": self.client_order_id,
            "side": self.side,
            "quantity": self.quantity,
            "price_ticks": self.price_ticks,
            "ts": self.ts,
            "quote_event_id": self.quote_event_id,
            "quote_bid_ticks": self.quote_bid_ticks,
            "quote_ask_ticks": self.quote_ask_ticks,
            "quote_age_us": self.quote_age_us,
            "extra_slippage_ticks": self.extra_slippage_ticks,
            "after_cancel_request": self.after_cancel_request,
            "delayed_for_liquidity": self.delayed_for_liquidity,
        }

    @staticmethod
    def from_record(r: dict) -> "Fill":
        return Fill(**r)
