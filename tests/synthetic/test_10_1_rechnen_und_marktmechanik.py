"""Handbuch Kap. 10.1: Rechnen und Marktmechanik (T01–T08)."""
from decimal import Decimal
from fractions import Fraction

from orderflow.common.units import load_contract
from orderflow.dataadapter.events import normalize
from orderflow.execution.costs import expectancy_r, short_trade_pnl
from orderflow.execution.fifo import fifo_limit_fill
from orderflow.execution.risk import position_size
from orderflow.features.core import FLAG_NO_CLASSIFIED_VOLUME, FLAG_ZERO_VOLUME, window_stats
from orderflow.features.mechanics import classify_bid_reduction
from orderflow.rules import reasons as R
from orderflow.verifier.claims import check_statement

MES = load_contract("MES")


def window_gate_reasons(stats, policy):
    """Qualitäts- und Signalgates eines Fensters, wie im Regelmodul (Kap. 7.1)."""
    out = []
    if stats.known_fraction is None or stats.known_fraction < policy.min_known_fraction:
        out.append(R.NO_TRADE_DATA_QUALITY)
    if stats.relative_delta is None or stats.relative_delta < policy.min_relative_delta:
        out.append(R.NO_SIGNAL)
    return out


def test_T01_delta(selftest, policy):
    st = selftest("T01", "Delta", "10.1")
    s = window_stats(140, 90, 0)
    st.check("V", s.total, 230)
    st.check("D", s.delta, 50)
    st.check("DR", s.relative_delta, Fraction(50, 230))
    st.check("DR_float_6", round(float(s.relative_delta), 6), 0.217391)
    st.check("K", s.known_fraction, Fraction(1))
    wrong = check_statement("Es gab 50 mehr gekaufte als verkaufte Kontrakte.")
    st.check("Begriffsprüfung_falsche_Aussage", [f.code for f in wrong], ["DELTA_AS_NET_VOLUME"])
    right = check_statement("Delta +50: Das aggressive Kaufvolumen übersteigt das aggressive Verkaufsvolumen "
                            "um 50. Insgesamt wurden 230 Kontrakte gekauft und verkauft.")
    st.check("Begriffsprüfung_korrekte_Aussage", right, [])


def test_T02_unbekannte_seite(selftest, policy):
    st = selftest("T02", "Unbekannte Seite", "10.1")
    s = window_stats(140, 90, 70)
    st.check("V", s.total, 300)
    st.check("D_bekannter_Anteil", s.delta, 50)
    st.check("K", s.known_fraction, Fraction(230, 300))
    st.check("K_float_6", round(float(s.known_fraction), 6), 0.766667)
    st.check("DR", s.relative_delta, Fraction(50, 230))
    st.check("Entscheidung", window_gate_reasons(s, policy), [R.NO_TRADE_DATA_QUALITY])


def test_T03_leeres_fenster(selftest, policy):
    st = selftest("T03", "Leeres Fenster", "10.1")
    s = window_stats(0, 0, 0)
    st.check("V", s.total, 0)
    st.check("D", s.delta, 0)
    st.check("DR", s.relative_delta, None)
    st.check("K", s.known_fraction, None)
    st.check("Qualitätsflags", s.flags, (FLAG_ZERO_VOLUME, FLAG_NO_CLASSIFIED_VOLUME))
    st.true("kein_Signal", R.NO_SIGNAL in window_gate_reasons(s, policy)
            and R.NO_TRADE_DATA_QUALITY in window_gate_reasons(s, policy))


def test_T04_positionsgroesse(selftest, policy, sim):
    st = selftest("T04", "Positionsgröße", "10.1")
    st.check("Tickwert", MES.tick_value_usd, Decimal("1.25"))
    st.check("Gebühr", sim.roundtrip_fee_usd, Decimal("2.50"))
    st.check("Reserve_Ticks", sim.sizing_slippage_reserve_ticks, 2)
    r50 = position_size(Decimal("50"), 20, MES.tick_value_usd, sim.roundtrip_fee_usd,
                        sim.sizing_slippage_reserve_ticks, policy.max_contracts)
    st.check("Risiko_je_Kontrakt", r50.risk_per_contract, Decimal("30.00"))
    st.check("rechnerisch", r50.by_budget, 1)
    st.check("nach_Referenzlimit", r50.quantity, 1)
    r25 = position_size(Decimal("25"), 20, MES.tick_value_usd, sim.roundtrip_fee_usd,
                        sim.sizing_slippage_reserve_ticks, policy.max_contracts)
    st.check("Budget_25_Menge", r25.quantity, 0)
    st.check("Budget_25_Entscheidung", R.NO_TRADE_RISK if r25.quantity == 0 else "TRADE", R.NO_TRADE_RISK)


def test_T05_nettoergebnis(selftest, sim):
    st = selftest("T05", "Nettoergebnis", "10.1")
    pnl = short_trade_pnl(MES.price_to_ticks("5000.00"), MES.price_to_ticks("4998.00"), 1, MES,
                          sim.roundtrip_fee_usd)
    st.check("Ticks", pnl.ticks, 8)
    st.check("brutto_USD", pnl.gross_usd, Decimal("10.00"))
    st.check("netto_USD", pnl.net_usd, Decimal("7.50"))
    st.check("abgezogene_Kosten_nur_Gebühr", pnl.fees_usd, Decimal("2.50"))


def test_T06_kosten_machen_vorteil_negativ(selftest):
    st = selftest("T06", "Kosten machen den Vorteil negativ", "10.1")
    e = expectancy_r(Fraction(4, 10), Fraction(2), Fraction(6, 10), Fraction(1), Fraction(1, 4))
    st.check("Erwartungswert_R", e, Fraction(-5, 100))
    st.true("negativ", e < 0, e)


def _book(**kw):
    rec = {"schema_version": "1.0", "event_id": kw.pop("event_id"), "venue": "CME", "instrument_id": "MES_TEST",
           "session_id": "S", "stream_id": kw.pop("stream_id", "book-A"), "sequence": kw.pop("sequence"),
           "exchange_ts": kw.pop("ts"), "receive_ts": None, "source_hash": "fixture-T07", **kw}
    return normalize(rec, 20)


def test_T07_annulliertes_angebot(selftest):
    st = selftest("T07", "Annulliertes Angebot", "10.1")
    level = MES.price_to_ticks("4999.00")
    book = [
        _book(event_id="b1", sequence=1, ts="2026-09-22T14:00:00.000Z", event_type="BOOK", book_side="BID",
              action="ADD", quantity=500, quantity_semantics="NEW_TOTAL", price_ticks=level, book_type="MBP"),
        _book(event_id="b2", sequence=2, ts="2026-09-22T14:00:01.000Z", event_type="BOOK", book_side="BID",
              action="DELETE", quantity=0, quantity_semantics="NEW_TOTAL", price_ticks=level, book_type="MBP"),
    ]
    finding = classify_bid_reduction(book, trades=[], price_ticks=level)
    st.check("beobachtbar", finding.observable, "BID_MODIFIED_OR_CANCELLED")
    st.check("aggressiver_Verkauf_bewiesen", finding.aggressive_sell_proven, False)
    st.check("Manipulationsabsicht_bewiesen", finding.manipulation_intent_proven, False)
    st.check("Identität_bewiesen", finding.participant_identity_proven, False)
    bad = check_statement("Das ist Spoofing durch einen institutionellen Händler.")
    st.check("Begriffsprüfung", sorted(f.code for f in bad), ["IDENTITY_CLAIM", "MANIPULATION_CLAIM"])
    ok = check_statement("Eine Manipulationsabsicht ist daraus nicht bewiesen.")
    st.check("zulässige_Abgrenzung", ok, [])


def test_T08_limit_beruehrt(selftest):
    st = selftest("T08", "Limit berührt (FIFO-Testmodell)", "10.1")
    r = fifo_limit_fill(queue_ahead=100, executed_at_level=10, cancels_ahead=0, own_quantity=1)
    st.check("eigener_Fill", r.own_filled, 0)
    st.check("Restqueue_davor", r.queue_ahead_remaining, 90)
    st.check("Modell", r.model, "FIFO_TEST_MODEL")
    st.check("keine_allgemeine_Börsenregel", r.general_exchange_rule, False)
