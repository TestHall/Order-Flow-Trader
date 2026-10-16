"""Handbuch Kap. 10.3: Daten und Betrieb (T15–T20)."""
from datetime import date
from pathlib import Path

import pytest

from orderflow.common.contracts import EntryIntent
from orderflow.common.timeutil import format_ts, parse_ts
from orderflow.execution.orders import OrderManager
from orderflow.features.session import CalendarUnavailable, TradingCalendar
from orderflow.journal.journal import Journal
from orderflow.replay.controller import SessionRunner
from orderflow.replay.synthetic import TRADES
from orderflow.rules import reasons as R
from orderflow.verifier.experiments import ExperimentRegistry
from orderflow.verifier.invariants import InvariantMonitor
from orderflow.verifier.timing import verify_snapshots
from tests.scenarios import (CONFIRM_LEVEL, CONTRACT, POLICY, SIM, P, add_breakout, base_session, run)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
M = CONTRACT.price_to_ticks


def _t15_session(late_size: int, late_side: str):
    s = base_session(tag="T15")
    add_breakout(s)
    s.bar(s.at("10:16"), [P + 1, P, P - 1, CONFIRM_LEVEL], 60, 60)
    s.quote(s.at("10:17:00.300"), CONFIRM_LEVEL, CONFIRM_LEVEL + 1)
    s.quiet_bars(s.at("10:17"), 3, P - 14, P - 10)
    # Börsenzeit im Ausbruchsbalken, aber erst um 10:18:00 empfangen.
    s.trade(s.at("10:15:30.500"), P + 2, late_size, late_side,
            receive_delay_ms=s.at("10:18:00") // 1000 - s.at("10:15:30.500") // 1000)
    return s


def test_T15_replay_zukunft(selftest):
    st = selftest("T15", "Replay-Zukunft", "10.3")
    r1, a = run(_t15_session(5, "BUY"))
    r2, b = run(_t15_session(1000, "SELL"))   # spätere Zeile verändert
    cutoff = "2026-09-22T14:18:00.000Z"
    before_a = [d for d in a.decisions if d["decided_at"] < cutoff]
    before_b = [d for d in b.decisions if d["decided_at"] < cutoff]
    st.true("Vergleichsmenge_nicht_leer", len(before_a) > 40, len(before_a))
    st.check("früherer_Output_identisch", before_a == before_b, True)
    armed = [d for d in a.decisions if d["reason_codes"] == [R.WAIT_CONFIRMATION]][0]
    st.check("Ausbruchsbalken_ohne_späte_Zeile_B_S", (armed["facts"]["buy_volume"], armed["facts"]["sell_volume"]),
             (140, 90))
    st.check("späte_Zeile_als_LATE_EVENT", len(b.late_events), 1)
    st.check("Entscheidung_vor_Kenntnis_PROPOSE_SHORT_in_beiden",
             [d["action"] for d in before_a if d["action"] == "PROPOSE_SHORT"],
             [d["action"] for d in before_b if d["action"] == "PROPOSE_SHORT"])
    st.check("Snapshot_Zeitprüfung_A", verify_snapshots(list(r1.journal.records()), r1._events), [])
    st.check("Snapshot_Zeitprüfung_B", verify_snapshots(list(r2.journal.records()), r2._events), [])


def test_T16_doppelte_order(selftest):
    st = selftest("T16", "Doppelte Order", "10.3")
    j = Journal()
    om = OrderManager(j)
    o1, created1 = om.submit("S-C1-ENTRY", "ENTRY", "SELL", 1, 1, 100)
    o2, created2 = om.submit("S-C1-ENTRY", "ENTRY", "SELL", 1, 1, 200)
    st.check("eine_logische_Order", len(om.orders), 1)
    st.check("zweiter_Aufruf_liefert_bestehenden_Status", (o2 is o1, created2, o2.status), (True, False, o1.status))
    st.check("Journal_ORDER_Einträge", len(list(j.records("ORDER"))), 1)
    over, _ = om.submit("S-C2-ENTRY", "ENTRY", "SELL", 2, 1, 300)
    st.check("Menge_über_Genehmigung_abgewiesen", (over.status, over.reasons), ("REJECTED", ["QUANTITY_ABOVE_APPROVED"]))
    # Auf Ebene des Ausführungsmoduls: dieselbe Absicht zweimal gesendet.
    s = base_session(tag="T16")
    add_breakout(s)
    s.bar(s.at("10:16"), [P + 1, P, P - 1, CONFIRM_LEVEL], 60, 60)
    s.quote(s.at("10:17:00.300"), CONFIRM_LEVEL, CONFIRM_LEVEL + 1)
    runner, _ = run(s, until=s.at("10:17:00.200"))
    order = next(runner.journal.records("ORDER"))["payload"]
    intent = EntryIntent(order["client_order_id"], order["meta"]["decision_id"], 1, "SELL", 1, 1,
                         order["meta"]["stop_ticks"], 2, s.at("10:17:00.100"), order["meta"]["valid_until"],
                         s.at("15:55"))
    runner.execution.submit_entry(intent, runner.scheduler.now)
    runner.run(until=s.at("10:17:05"))
    st.check("Retry_mit_gleicher_ID_keine_zweite_Order", len(list(runner.journal.records("ORDER"))), 1)
    st.check("maximal_genehmigte_Menge_gefüllt", [f["payload"]["quantity"] for f in runner.journal.records("FILL")], [1])


def _t17_session():
    s = base_session(tag="T17")
    add_breakout(s)
    s.bar(s.at("10:16"), [P + 1, P, P - 1, CONFIRM_LEVEL], 60, 60)
    s.quote(s.at("10:17:00.300"), CONFIRM_LEVEL, CONFIRM_LEVEL + 1)
    s.quiet_bars(s.at("10:17"), 30, P - 6, P - 3)        # bewegt sich nicht bis Ziel/Stopp
    add_breakout(s, "10:47")                              # späterer Ausbruch: darf keinen 2. Entry erzeugen
    s.bar(s.at("10:48"), [P + 1, P, P - 1, CONFIRM_LEVEL], 60, 60)
    s.quiet_bars(s.at("10:49"), 10, P - 14, P - 10)
    return s


def test_T17_neustart_bei_position(selftest, tmp_path):
    st = selftest("T17", "Neustart bei Position", "10.3")
    s = _t17_session()
    path = tmp_path / "journal.jsonl"
    crash = s.at("10:17:05")
    r1, res1 = run(s, journal_path=path, until=crash)
    st.check("vor_Absturz_offen", (res1.final_state, r1.orders.position()), ("OPEN", -1))
    del r1  # Prozess "stirbt"; nur das Journal auf der Platte bleibt
    mon = InvariantMonitor()
    r2 = SessionRunner(s.events(SIM), s.context(P), POLICY, SIM, CONTRACT, Journal(path), on_step=mon,
                       resume_at=crash)
    r2.run(until=crash + 1)
    pos = r2.engine.position
    st.check("Position_rekonstruiert", (r2.engine.state.value, r2.orders.position(), pos.quantity), ("OPEN", -1, 1))
    st.check("Schutzstatus_Stopp", CONTRACT.ticks_to_price(pos.stop_ticks), CONTRACT.ticks_to_price(M("5001.50")))
    st.check("Schutzstatus_Ziel", CONTRACT.ticks_to_price(pos.target_ticks), CONTRACT.ticks_to_price(M("4995.50")))
    st.check("Zeitausstieg", format_ts(pos.time_exit_at), "2026-09-22T14:27:00.350Z")
    res2 = r2.run()
    entries = [r for r in r2.journal.records("ORDER") if r["payload"]["kind"] == "ENTRY"]
    st.check("keine_zweite_Entry_Order", len(entries), 1)
    st.check("nicht_auf_flat_zurückgesetzt_vor_Exit", [x for x in res2.trace if x[2] == "IDLE"], [])
    st.check("Exit_durch_Zeitausstieg", [(r["exit_reason"], r["exit_ts"]) for r in res2.results],
             [("TIME", "2026-09-22T14:27:00.600Z")])
    st.check("Endzustand", res2.final_state, "DONE_FOR_SESSION")
    st.true("Invarianten_nach_Neustart_geprüft", mon.checks > 0, mon.checks)


def test_T18_datenluecke_bei_offenem_trade(selftest):
    st = selftest("T18", "Datenlücke bei offenem Trade", "10.3")
    s = base_session(tag="T18")
    add_breakout(s)
    s.bar(s.at("10:16"), [P + 1, P, P - 1, CONFIRM_LEVEL], 60, 60)
    s.quote(s.at("10:17:00.300"), CONFIRM_LEVEL, CONFIRM_LEVEL + 1)
    # In der Lücke liegen sowohl ein Stopp- als auch ein Ziel-Trade; die Reihenfolge ist unbekannt.
    s.trade(s.at("10:17:20"), M("5001.50"), 5, "BUY")
    s.trade(s.at("10:17:25"), M("4995.50"), 5, "SELL")
    s.drop(TRADES, s.at("10:17:15"), s.at("10:17:30"))
    s.bar(s.at("10:18"), [M("4996.00"), M("4995.00")], 0, 120)
    s.quiet_bars(s.at("10:19"), 3, P - 24, P - 20)
    runner, res = run(s, until=s.at("10:22"))
    st.check("Lücke_erkannt", [q["kind"] for q in res.quality if q["kind"] == "GAP"], ["GAP"])
    st.check("Trade_im_Bericht", len(res.results), 1)
    st.check("Ergebnis", res.results[0]["outcome"], "UNSCORABLE")
    st.check("Grund", res.results[0]["unscorable_reasons"], [R.DATA_GAP])
    st.check("nicht_als_Gewinn_gewertet", res.summary()["net_usd_scored"], "0")
    st.check("Schutz_blieb_aktiv_Exit_nach_Regeln", res.results[0]["exit_reason"], "TARGET")
    halts = [d for d in res.decisions if d["action"] == "HALT_NEW_ENTRIES"]
    st.check("neue_Einstiege_gesperrt", [d["reason_codes"] for d in halts], [[R.DATA_GAP]])


def test_T19_session_und_sommerzeit(selftest):
    st = selftest("T19", "Session und Sommerzeit", "10.3")
    cal = TradingCalendar.load(FIXTURES / "calendar_dst_test.toml")
    d1, d2 = date(2026, 3, 6), date(2026, 3, 9)    # vor und nach Beginn der US-Sommerzeit (8.3.2026)
    w1 = cal.reference_window(d1, POLICY.window_start, POLICY.window_end)
    w2 = cal.reference_window(d2, POLICY.window_start, POLICY.window_end)
    st.check("Start_06.03_UTC", format_ts(w1[0]), "2026-03-06T14:30:00.000Z")
    st.check("Start_09.03_UTC", format_ts(w2[0]), "2026-03-09T13:30:00.000Z")
    st.check("gleiche_lokale_Startzeit_versch_Offset", (w1[0] - parse_ts("2026-03-06T00:00:00Z")) -
             (w2[0] - parse_ts("2026-03-09T00:00:00Z")), 3_600_000_000)
    probe = "T13:45:00Z"
    st.check("13:45Z_am_09.03_in_Sitzung", cal.session_of(parse_ts("2026-03-09" + probe), "09:30", "16:00"), d2)
    st.check("13:45Z_am_06.03_vor_Sitzung", cal.session_of(parse_ts("2026-03-06" + probe), "09:30", "16:00"), None)
    st.check("Feiertag_kein_Handelstag", cal.is_trading_day(date(2026, 3, 11)), False)
    st.check("verkürzte_Sitzung_Ende", format_ts(cal.reference_window(date(2026, 3, 12), "09:30", "16:00")[1]),
             "2026-03-12T17:00:00.000Z")
    with pytest.raises(CalendarUnavailable):
        cal.reference_window(date(2026, 3, 16), "09:30", "16:00")
    st.true("nicht_abgedeckter_Tag_nicht_prüfbar", True)


def test_T20_verbotene_uebernahme(selftest, tmp_path):
    st = selftest("T20", "Verbotene Übernahme", "10.3")
    reg = ExperimentRegistry(tmp_path / "registry.json")
    reg.add_period("DEV", "2026-01-05", "2026-03-31", "DEVELOPMENT")
    reg.add_period("HOLD-1", "2026-04-01", "2026-05-29", "HOLDOUT")
    reg.register_attempt("OF-A-1.0-ALT1", "Volumenschwelle 2,0 x Median")
    reg.record_view("HOLD-1", "OF-A-1.0", "Baseline-Auswertung")
    reg.record_view("HOLD-1", "OF-A-1.0-ALT1", "Kandidat nach Sichtung angepasst")
    a = reg.assess_claim("OF-A-1.0-ALT1", "HOLD-1", improved=True)
    st.check("kein_unabhängiger_Nachweis", a.independent_evidence, False)
    st.check("Befund", a.finding, "INCONCLUSIVE")
    st.check("Zeitraum_als_Validierung_verbraucht",
             (reg.periods["HOLD-1"].role, reg.periods["HOLD-1"].status), ("VALIDATION", "CONSUMED_AS_VALIDATION"))
    st.check("neuer_Holdout_nötig", a.new_holdout_required, True)
    with pytest.raises(ValueError):
        reg.reserve_holdout("HOLD-X", "2026-05-01", "2026-06-30")   # überlappt / nicht später
    new = reg.reserve_holdout("HOLD-2", "2026-06-01", "2026-07-31")
    st.check("neuer_Holdout_reserviert", (new.role, new.status), ("HOLDOUT", "UNTOUCHED"))
    st.check("Register_persistiert", ExperimentRegistry(tmp_path / "registry.json").periods["HOLD-1"].status,
             "CONSUMED_AS_VALIDATION")
