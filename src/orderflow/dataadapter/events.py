"""Kanonisches Marktereignis (Kap. 5.1) als unveränderlicher Datensatz."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from orderflow.common import schema
from orderflow.common.hashing import hash_obj
from orderflow.common.timeutil import MS, parse_ts
from orderflow.dataadapter.fastcheck import fast_valid


@dataclass(frozen=True, slots=True)
class MarketEvent:
    event_id: str
    stream_id: str
    sequence: int
    event_type: str
    instrument_id: str
    session_id: str
    exchange_ts: int
    receive_ts: int
    receive_ts_assumed: bool
    event_hash: str
    price_ticks: int | None = None
    size: int | None = None
    aggressor: str | None = None
    classification_method: str | None = None
    bid_ticks: int | None = None
    ask_ticks: int | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    book_side: str | None = None
    action: str | None = None
    quantity: int | None = None
    quantity_semantics: str | None = None
    book_type: str | None = None
    order_id: str | None = None
    corrects_event_id: str | None = None
    market_status: str | None = None
    text: str | None = None


def normalize(record: dict[str, Any], assumed_receive_latency_ms: int) -> MarketEvent:
    """Validiert einen Rohdatensatz gegen das Schema und normalisiert ihn.

    Fehlt receive_ts, wird exchange_ts + angenommene Verzögerung verwendet und das Ereignis
    markiert: dann ist nur ein Börsenzeit-Replay mit angenommener Verzögerung möglich (Kap. 4.2).
    """
    if not fast_valid(record):
        schema.validate(record, "market_event")  # maßgeblich; liefert die genaue Fehlermeldung
    exchange_ts = parse_ts(record["exchange_ts"])
    raw_receive = record.get("receive_ts")
    if raw_receive is None:
        receive_ts, assumed = exchange_ts + assumed_receive_latency_ms * MS, True
    else:
        receive_ts, assumed = parse_ts(raw_receive), False
    return MarketEvent(
        event_id=record["event_id"],
        stream_id=record["stream_id"],
        sequence=record["sequence"],
        event_type=record["event_type"],
        instrument_id=record["instrument_id"],
        session_id=record["session_id"],
        exchange_ts=exchange_ts,
        receive_ts=receive_ts,
        receive_ts_assumed=assumed,
        event_hash=hash_obj(record),
        price_ticks=record.get("price_ticks"),
        size=record.get("size_contracts"),
        aggressor=record.get("aggressor"),
        classification_method=record.get("classification_method"),
        bid_ticks=record.get("bid_ticks"),
        ask_ticks=record.get("ask_ticks"),
        bid_size=record.get("bid_size"),
        ask_size=record.get("ask_size"),
        book_side=record.get("book_side"),
        action=record.get("action"),
        quantity=record.get("quantity"),
        quantity_semantics=record.get("quantity_semantics"),
        book_type=record.get("book_type"),
        order_id=record.get("order_id"),
        corrects_event_id=record.get("corrects_event_id"),
        market_status=record.get("market_status"),
        text=record.get("text"),
    )


def normalize_all(records: Iterable[dict[str, Any]], assumed_receive_latency_ms: int) -> list[MarketEvent]:
    return [normalize(r, assumed_receive_latency_ms) for r in records]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def knowledge_order(events: Iterable[MarketEvent]) -> list[MarketEvent]:
    """Reihenfolge, in der Informationen verfügbar wurden: Empfangszeit, dann Stream und Sequenz."""
    return sorted(events, key=lambda e: (e.receive_ts, e.stream_id, e.sequence))
