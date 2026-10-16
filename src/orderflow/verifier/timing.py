"""Prüfung auf Zukunftsverletzungen (Kap. 5.2, 14.1, T15).

Jeder Snapshot muss genau ein Präfix der in Empfangsreihenfolge verarbeiteten Ereignisse abdecken:
alle enthaltenen Ereignisse wurden bis as_of_receive_ts empfangen, und der Digest stimmt mit einer
unabhängigen Neuberechnung überein.
"""
from __future__ import annotations

from typing import Any

from orderflow.common.hashing import GENESIS, chain
from orderflow.common.timeutil import parse_ts
from orderflow.dataadapter.events import MarketEvent, knowledge_order


def _processed_prefix(events: list[MarketEvent]) -> list[MarketEvent]:
    seen: set[tuple[str, int]] = set()
    last: dict[str, int] = {}
    out = []
    for e in knowledge_order(events):
        key = (e.stream_id, e.sequence)
        if key in seen or (e.stream_id in last and e.sequence < last[e.stream_id]):
            seen.add(key)
            continue
        seen.add(key)
        last[e.stream_id] = e.sequence
        out.append(e)
    return out


def verify_snapshots(journal_records: list[dict[str, Any]], events: list[MarketEvent]) -> list[str]:
    processed = _processed_prefix(events)
    digests = [GENESIS]
    for e in processed:
        digests.append(chain(digests[-1], e.event_hash))
    violations = []
    snapshot_ids = set()
    for r in journal_records:
        if r["kind"] == "SNAPSHOT":
            s = r["payload"]
            snapshot_ids.add(s["snapshot_id"])
            n = s["event_count"]
            as_of = parse_ts(s["as_of_receive_ts"])
            if as_of != r["ts"]:
                violations.append(f"{s['snapshot_id']}: as_of != Protokollzeit")
            if n > len(processed) or digests[n] != s["events_digest"]:
                violations.append(f"{s['snapshot_id']}: Digest passt zu keinem Ereignispräfix")
                continue
            if n and processed[n - 1].receive_ts > as_of:
                violations.append(f"{s['snapshot_id']}: enthält erst nach as_of empfangene Ereignisse")
        elif r["kind"] == "DECISION":
            if r["payload"]["snapshot_id"] not in snapshot_ids:
                violations.append(f"{r['payload']['decision_id']}: Entscheidung ohne vorher gespeicherten Snapshot")
    return violations
