"""Pflichtprüfungen je Stream (Kap. 4.2): doppelte Ereignisse, Sequenzlücken, Reihenfolge, Quotes.

Sequenzen werden je stream_id geprüft; unterschiedliche Kanäle sind keine gemeinsame Sequenz.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from orderflow.dataadapter.events import MarketEvent


class Verdict(str, Enum):
    OK = "OK"
    DUPLICATE = "DUPLICATE"                 # identisches Ereignis erneut -> verwerfen
    CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"  # gleiche ID/Sequenz, anderer Inhalt
    GAP = "GAP"                             # Sequenzlücke vor diesem Ereignis
    OUT_OF_ORDER = "OUT_OF_ORDER"           # ältere Sequenz, nicht als Duplikat erkennbar


@dataclass
class QualityIssue:
    verdict: Verdict
    stream_id: str
    receive_ts: int
    detail: str
    gap_from_exchange_ts: int | None = None
    gap_to_exchange_ts: int | None = None


@dataclass
class StreamChecker:
    last_seq: dict[str, int] = field(default_factory=dict)
    last_exchange_ts: dict[str, int] = field(default_factory=dict)
    seen: dict[tuple[str, int], str] = field(default_factory=dict)
    issues: list[QualityIssue] = field(default_factory=list)

    def check(self, e: MarketEvent) -> Verdict:
        key = (e.stream_id, e.sequence)
        if key in self.seen:
            verdict = Verdict.DUPLICATE if self.seen[key] == e.event_hash else Verdict.CONFLICTING_DUPLICATE
            self.issues.append(QualityIssue(verdict, e.stream_id, e.receive_ts, f"seq {e.sequence}"))
            return verdict
        self.seen[key] = e.event_hash
        last = self.last_seq.get(e.stream_id)
        verdict = Verdict.OK
        if last is not None and e.sequence < last:
            verdict = Verdict.OUT_OF_ORDER
            self.issues.append(QualityIssue(verdict, e.stream_id, e.receive_ts, f"seq {e.sequence} < {last}"))
            return verdict
        if last is not None and e.sequence > last + 1:
            verdict = Verdict.GAP
            self.issues.append(
                QualityIssue(
                    verdict,
                    e.stream_id,
                    e.receive_ts,
                    f"fehlend {last + 1}..{e.sequence - 1}",
                    gap_from_exchange_ts=self.last_exchange_ts.get(e.stream_id, e.exchange_ts),
                    gap_to_exchange_ts=e.exchange_ts,
                )
            )
        self.last_seq[e.stream_id] = e.sequence
        self.last_exchange_ts[e.stream_id] = e.exchange_ts
        return verdict


def quote_flags(e: MarketEvent) -> list[str]:
    flags = []
    if e.bid_ticks is not None and e.ask_ticks is not None and e.bid_ticks >= e.ask_ticks:
        flags.append("CROSSED_OR_LOCKED_QUOTE")
    if e.bid_size == 0 or e.ask_size == 0:
        flags.append("EMPTY_SIDE")
    return flags
