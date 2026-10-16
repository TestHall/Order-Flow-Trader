"""Unabhängige Prüfung jedes Fills gegen das Ausführungsmodell (Kap. 8.2, 9 Stufe D, 14.1).

Hartes Gate: null erfundene Fills. Ein Fill ist nur gültig, wenn zum Fill-Zeitpunkt der zuletzt
empfangene Quote genau der protokollierte war, der Preis dessen Bid (Verkauf) bzw. Ask (Rückkauf)
zuzüglich der deklarierten Zusatz-Slippage ist, die Menge sichtbar war und die Orderverzögerung
eingehalten wurde.
"""
from __future__ import annotations

import bisect
from typing import Any, Iterable

from orderflow.dataadapter.events import MarketEvent, knowledge_order


def _accepted_quotes(events: Iterable[MarketEvent]) -> list[MarketEvent]:
    seen: set[tuple[str, int]] = set()
    last: dict[str, int] = {}
    out = []
    for e in knowledge_order(events):
        key = (e.stream_id, e.sequence)
        if key in seen or (e.stream_id in last and e.sequence < last[e.stream_id]):
            continue
        seen.add(key)
        last[e.stream_id] = e.sequence
        if e.event_type == "QUOTE":
            out.append(e)
    return out


def verify_fills(journal_records: list[dict[str, Any]], events: list[MarketEvent],
                 order_latency_us: int, exit_latency_us: int) -> list[str]:
    quotes = _accepted_quotes(events)
    q_ts = [q.receive_ts for q in quotes]
    orders = {r["payload"]["client_order_id"]: r for r in journal_records if r["kind"] == "ORDER"}
    violations = []
    for r in journal_records:
        if r["kind"] != "FILL":
            continue
        f = r["payload"]
        cid = f["client_order_id"]
        if cid not in orders:
            violations.append(f"{cid}: Fill ohne Order")
            continue
        o = orders[cid]["payload"]
        i = bisect.bisect_right(q_ts, f["ts"]) - 1
        if i < 0:
            violations.append(f"{cid}: Fill ohne vorher empfangenen Quote")
            continue
        q = quotes[i]
        if q.event_id != f["quote_event_id"]:
            violations.append(f"{cid}: Fill nutzt {f['quote_event_id']}, zuletzt empfangen war {q.event_id}")
        slip = f["extra_slippage_ticks"]
        if f["side"] == "SELL":
            expected, visible = q.bid_ticks - slip, q.bid_size
        else:
            expected, visible = q.ask_ticks + slip, q.ask_size
        if f["price_ticks"] != expected:
            violations.append(f"{cid}: Preis {f['price_ticks']} != {expected} aus sichtbarem Quote")
        if f["quantity"] > visible:
            violations.append(f"{cid}: Menge {f['quantity']} > sichtbar {visible}")
        if f["quantity"] > o["quantity"]:
            violations.append(f"{cid}: Fill größer als Order")
        latency = order_latency_us if o["kind"] == "ENTRY" else exit_latency_us
        if f["ts"] < o["created_at"] + latency:
            violations.append(f"{cid}: Fill vor Ablauf der Orderverzögerung")
    return violations
