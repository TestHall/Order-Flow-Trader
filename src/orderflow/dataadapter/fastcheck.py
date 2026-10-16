"""Schneller Vorabtest für Marktereignisse, äquivalent zu schemas/market_event.schema.json.

jsonschema kostet ~0,2 ms je Ereignis; für Millionen Ereignisse zu langsam. Dieser Test darf nie
etwas akzeptieren, das das Schema ablehnt: tests/unit/test_fastcheck.py vergleicht beide auf
gültigen und mutierten Datensätzen. Abgelehnte Datensätze laufen zur Fehlermeldung immer durch
jsonschema.
"""
from __future__ import annotations

import re
from typing import Any

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$")
_STR_REQ = ("event_id", "venue", "instrument_id", "session_id", "stream_id", "source_hash")
_ENUMS = {
    "event_type": {"TRADE", "QUOTE", "BOOK", "TRADE_CORRECTION", "TRADE_BUST", "STATUS"},
    "aggressor": {"BUY", "SELL", "UNKNOWN"},
    "classification_method": {"EXCHANGE", "QUOTE_RULE", "TICK_RULE", "NONE"},
    "book_side": {"BID", "ASK"},
    "action": {"ADD", "MODIFY", "DELETE"},
    "quantity_semantics": {"NEW_TOTAL", "CHANGE"},
    "book_type": {"MBO", "MBP"},
    "market_status": {"OPEN", "HALT", "CLOSED"},
}
_INTS = ("price_ticks", "bid_ticks", "ask_ticks")
_NONNEG = ("sequence", "size_contracts", "bid_size", "ask_size", "quantity")
_NONEMPTY_OPT = ("order_id", "corrects_event_id")
_ALLOWED = set(_STR_REQ) | set(_ENUMS) | set(_INTS) | set(_NONNEG) | set(_NONEMPTY_OPT) | {
    "schema_version", "exchange_ts", "receive_ts", "text"}
_REQUIRED_BY_TYPE = {
    "TRADE": ("price_ticks", "size_contracts", "aggressor", "classification_method"),
    "QUOTE": ("bid_ticks", "ask_ticks", "bid_size", "ask_size"),
    "BOOK": ("book_side", "action", "quantity", "quantity_semantics", "price_ticks", "book_type"),
    "TRADE_CORRECTION": ("corrects_event_id", "price_ticks", "size_contracts", "aggressor", "classification_method"),
    "TRADE_BUST": ("corrects_event_id",),
    "STATUS": ("market_status",),
}


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def fast_valid(r: Any) -> bool:
    if not isinstance(r, dict) or not _ALLOWED.issuperset(r):
        return False
    if r.get("schema_version") != "1.0":
        return False
    for k in _STR_REQ:
        v = r.get(k)
        if not isinstance(v, str) or not v:
            return False
    if "sequence" not in r or "exchange_ts" not in r or "event_type" not in r:
        return False
    ex = r["exchange_ts"]
    if not isinstance(ex, str) or not _TS.match(ex):
        return False
    if "receive_ts" in r:
        rv = r["receive_ts"]
        if rv is not None and (not isinstance(rv, str) or not _TS.match(rv)):
            return False
    for k, allowed in _ENUMS.items():
        if k in r and (not isinstance(r[k], str) or r[k] not in allowed):
            return False
    for k in _INTS:
        if k in r and not _is_int(r[k]):
            return False
    for k in _NONNEG:
        if k in r and (not _is_int(r[k]) or r[k] < 0):
            return False
    for k in _NONEMPTY_OPT:
        if k in r and (not isinstance(r[k], str) or not r[k]):
            return False
    if "text" in r and not isinstance(r["text"], str):
        return False
    et = r["event_type"]
    for k in _REQUIRED_BY_TYPE[et]:
        if k not in r:
            return False
    if et == "TRADE" and r["size_contracts"] < 1:
        return False
    if et == "BOOK" and r.get("book_type") == "MBO" and "order_id" not in r:
        return False
    return True
