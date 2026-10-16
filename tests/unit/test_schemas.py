"""Datenverträge Kap. 5: Beispiele aus dem Handbuch sind gültig, Verstöße werden abgewiesen."""
import copy

import pytest

from orderflow.common import schema
from orderflow.common.schema import SchemaViolation

EVENT_5_1 = {
    "schema_version": "1.0", "event_id": "synthetic-trade-001", "venue": "CME", "instrument_id": "MES_TEST",
    "session_id": "synthetic-session-A", "stream_id": "trades-A", "sequence": 101,
    "exchange_ts": "2026-09-22T14:00:00.100Z", "receive_ts": "2026-09-22T14:00:00.120Z", "event_type": "TRADE",
    "price_ticks": 20000, "size_contracts": 10, "aggressor": "BUY", "classification_method": "EXCHANGE",
    "source_hash": "fixture-A-v1",
}
DECISION_5_3 = {
    "decision_id": "decision-A-001", "snapshot_id": "snapshot-A-001", "policy_version": "OF-A-1.0",
    "mode": "REPLAY", "action": "NO_TRADE", "reason_codes": ["WAIT_CONFIRMATION"],
    "facts": {"buy_volume": 140, "sell_volume": 90, "unknown_volume": 0, "delta": 50, "known_fraction": 1.0},
    "evidence_refs": ["feature-window-A"], "hypothesis": "Aufnahme aggressiver Käufe möglich",
    "counterevidence": "Preisrückkehr noch nicht bestätigt", "would_invalidate": "Handel an der Invalidierungsgrenze",
    "proposed_order": None, "forecast": None,
}
REVIEW_13_3 = {
    "review_id": "review-017", "decision_id": "decision-017", "policy_version": "OF-A-1.0", "error_class": "POLICY",
    "evidence_refs": ["event-901", "rule-7.3"], "finding": "Invalidierung trat vor Bestätigung ein",
    "expected_action": "NO_TRADE", "observed_action": "PROPOSE_SHORT",
    "candidate_change": "Invalidierung sofort verriegeln",
    "falsification_test": "Kein Signal nach früherem Barrieretreffer", "status": "PROPOSED",
}
ORDER = {"client_order_id": "c1", "side": "SELL", "quantity": 1, "entry_rule": "MARKET_SELL_AT_BID_AFTER_LATENCY",
         "stop_ticks": 20006, "target_ticks": 19982, "valid_for_ms": 2000, "planned_risk_usd": "15.00"}


def test_handbuchbeispiele_sind_gueltig():
    schema.validate(EVENT_5_1, "market_event")
    schema.validate(DECISION_5_3, "decision")
    schema.validate(REVIEW_13_3, "review")


@pytest.mark.parametrize("field,value", [
    ("aggressor", "BUYER"), ("price_ticks", 5000.25), ("size_contracts", 0), ("size_contracts", -1),
    ("exchange_ts", "2026-09-22T16:00:00.100+02:00"), ("sequence", -1), ("schema_version", "2.0"),
    ("unbekanntes_feld", 1),
])
def test_marktereignis_verstoesse(field, value):
    bad = dict(EVENT_5_1, **{field: value})
    with pytest.raises(SchemaViolation):
        schema.validate(bad, "market_event")


@pytest.mark.parametrize("missing", ["price_ticks", "size_contracts", "aggressor", "classification_method"])
def test_trade_pflichtfelder(missing):
    bad = {k: v for k, v in EVENT_5_1.items() if k != missing}
    with pytest.raises(SchemaViolation):
        schema.validate(bad, "market_event")


def test_quote_und_mbo_pflichtfelder():
    q = dict(EVENT_5_1, event_type="QUOTE", bid_ticks=1, ask_ticks=2, bid_size=1)
    for k in ("price_ticks", "size_contracts", "aggressor", "classification_method"):
        q.pop(k)
    with pytest.raises(SchemaViolation):
        schema.validate(q, "market_event")          # ask_size fehlt
    q["ask_size"] = 3
    schema.validate(q, "market_event")
    book = {k: v for k, v in q.items() if k not in ("bid_ticks", "ask_ticks", "bid_size", "ask_size")}
    book.update(event_type="BOOK", book_side="BID", action="ADD", quantity=5, quantity_semantics="NEW_TOTAL",
                price_ticks=20000, book_type="MBO")
    with pytest.raises(SchemaViolation):
        schema.validate(book, "market_event")       # MBO ohne OrderID
    book["order_id"] = "o-1"
    schema.validate(book, "market_event")


def test_ordervorschlag_pflichtfelder():
    d = dict(DECISION_5_3, action="PROPOSE_SHORT", proposed_order=dict(ORDER))
    schema.validate(d, "decision")
    for k in ORDER:
        bad = copy.deepcopy(d)
        bad["proposed_order"].pop(k)
        with pytest.raises(SchemaViolation):
            schema.validate(bad, "decision")
    with pytest.raises(SchemaViolation):
        schema.validate(dict(DECISION_5_3, action="PROPOSE_SHORT", proposed_order=None), "decision")
    with pytest.raises(SchemaViolation):
        schema.validate(dict(d, proposed_order=dict(ORDER, side="BUY")), "decision")
    with pytest.raises(SchemaViolation):
        schema.validate(dict(DECISION_5_3, proposed_order=dict(ORDER)), "decision")  # NO_TRADE mit Order


def test_prognose_null_oder_kalibriert():
    with pytest.raises(SchemaViolation):
        schema.validate(dict(DECISION_5_3, forecast={"target_definition": "Ziel vor Stopp", "probability": 0.9}),
                        "decision")
    schema.validate(dict(DECISION_5_3, forecast={"target_definition": "Ziel vor Stopp", "probability": 0.31,
                                                 "calibration_ref": "calib-train-2026Q1"}), "decision")


def test_unbekannte_aktion_und_grund():
    with pytest.raises(SchemaViolation):
        schema.validate(dict(DECISION_5_3, action="BUY_NOW"), "decision")
    with pytest.raises(SchemaViolation):
        schema.validate(dict(DECISION_5_3, reason_codes=["TRUST_ME"]), "decision")


def test_reasons_modul_entspricht_schema():
    from orderflow.rules import reasons
    enum = set(schema.validator("decision").schema["$defs"]["reason_code"]["enum"])
    codes = {v for k, v in vars(reasons).items() if k.isupper()}
    assert codes <= enum
