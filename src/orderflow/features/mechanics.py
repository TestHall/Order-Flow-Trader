"""Zulässige Schlussfolgerungen aus Orderbuchbeobachtungen (Kap. 3.2, T07)."""
from __future__ import annotations

from dataclasses import dataclass

from orderflow.dataadapter.events import MarketEvent


@dataclass(frozen=True)
class BookChangeFinding:
    observable: str                       # was gemessen wurde
    executed_volume_at_level: int
    aggressive_sell_proven: bool
    manipulation_intent_proven: bool
    participant_identity_proven: bool
    category: str = "MEASURED_FACT"       # Kap. 3.5: Fakt, nicht Interpretation


def classify_bid_reduction(book_events: list[MarketEvent], trades: list[MarketEvent],
                           price_ticks: int) -> BookChangeFinding:
    """Bid-Menge auf einem Preisniveau sinkt. Beobachtbar ist nur Änderung/Stornierung bzw.,
    falls passende Trades existieren, Ausführung. Absicht oder Identität sind nie belegt."""
    executed = sum(t.size for t in trades if t.price_ticks == price_ticks and t.aggressor == "SELL")
    reduced = any(
        b.book_side == "BID" and b.price_ticks == price_ticks and b.action in ("DELETE", "MODIFY")
        for b in book_events
    )
    if not reduced:
        observable = "NO_REDUCTION"
    elif executed == 0:
        observable = "BID_MODIFIED_OR_CANCELLED"
    else:
        observable = "BID_PARTLY_OR_FULLY_EXECUTED"
    return BookChangeFinding(
        observable=observable,
        executed_volume_at_level=executed,
        aggressive_sell_proven=executed > 0,
        manipulation_intent_proven=False,
        participant_identity_proven=False,
    )
