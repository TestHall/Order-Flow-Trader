"""Handbuch Kap. 10.2: Strategiegrenzen (T09–T14), über den vollständigen Replay-Pfad.

Gemeinsame Annahmen: P = 5000,00, Tick = 0,25, H = 5001,00, L = 4999,75, gültiges Signalvolumen.
Bestätigungsgrenze 4999,50, Stopp 5001,50.
"""
from decimal import Decimal

from orderflow.common.timeutil import format_ts
from orderflow.rules import reasons as R
from orderflow.rules.engine import EntryFilterModel
from tests.scenarios import CONFIRM_LEVEL, CONTRACT, H, L, P, STOP, add_breakout, base_session, non_routine, run

M = CONTRACT.price_to_ticks


def _orders(runner, kind="ENTRY"):
    return [r for r in runner.journal.records("ORDER") if r["payload"]["kind"] == kind]


def test_gemeinsame_annahmen(selftest):
    st = selftest("T09-T12", "Gemeinsame Annahmen Kap. 10.2", "10.2")
    st.check("P", P, 20000)
    st.check("H", H, M("5001.00"))
    st.check("L", L, M("4999.75"))
    st.check("Bestätigungsgrenze_min(P-1,L-1)", min(P - 1, L - 1), CONFIRM_LEVEL)
    st.check("Bestätigungsgrenze_Preis", CONTRACT.ticks_to_price(CONFIRM_LEVEL), Decimal("4999.50"))
    st.check("Stopp_H+2", H + 2, STOP)
    st.check("Stopp_Preis", CONTRACT.ticks_to_price(STOP), Decimal("5001.50"))


def test_T09_kandidat_allein(selftest):
    st = selftest("T09", "Kandidat allein", "10.2")
    s = base_session()
    add_breakout(s)
    runner, res = run(s, until=s.at("10:16:00.100"))
    bar = runner.engine.bars[-1]
    st.check("Ausbruchsschluss", CONTRACT.ticks_to_price(bar.close), Decimal("5000.50"))
    st.check("Volumen_B_S_U", (bar.buy, bar.sell, bar.unknown), (140, 90, 0))
    d = res.decisions[-1]
    st.check("Zustand", res.final_state, "ARMED")
    st.check("Aktion", d["action"], "NO_TRADE")
    st.check("Gründe", d["reason_codes"], [R.WAIT_CONFIRMATION])
    st.check("H_eingefroren", runner.engine.candidate.high, H)
    st.check("L_eingefroren", runner.engine.candidate.low, L)
    st.check("Order_existiert", len(_orders(runner)), 0)


def _confirm_scenario(tag="A"):
    s = base_session(tag=tag)
    add_breakout(s)
    s.bar(s.at("10:16"), [P + 1, P, P - 1, CONFIRM_LEVEL], 60, 60)   # Schluss 4999,50
    s.quote(s.at("10:17:00.300"), CONFIRM_LEVEL, CONFIRM_LEVEL + 1)  # frischer Quote am Eintritt
    return s


def test_T10_bestaetigung(selftest):
    st = selftest("T10", "Bestätigung", "10.2")
    s = _confirm_scenario()
    s.quiet_bars(s.at("10:17"), 5, P - 14, P - 10)
    runner, res = run(s, until=s.at("10:17:30"))
    props = [d for d in res.decisions if d["action"] == "PROPOSE_SHORT"]
    st.check("vorgeschlagener_Short", len(props), 1)
    o = props[0]["proposed_order"]
    st.check("Seite", o["side"], "SELL")
    st.check("Stopp", CONTRACT.ticks_to_price(o["stop_ticks"]), Decimal("5001.50"))
    st.check("geplanter_Fill", CONTRACT.ticks_to_price(o["planned_fill_ticks"]), Decimal("4999.50"))
    st.check("Abstand_Ticks", o["distance_ticks"], 8)
    st.check("Ziel", CONTRACT.ticks_to_price(o["target_ticks"]), Decimal("4995.50"))
    st.check("Beispielrisiko_USD", Decimal(o["planned_risk_usd"]), Decimal("15.00"))
    fills = [r["payload"] for r in runner.journal.records("FILL")]
    st.check("Bid_Fill_nach_Verzögerung", [CONTRACT.ticks_to_price(f["price_ticks"]) for f in fills],
             [Decimal("4999.50")])
    st.check("Fillzeit", format_ts(fills[0]["ts"]), "2026-09-22T14:17:00.350Z")
    st.check("Order_erst_nach_Prüfungen", _orders(runner)[0]["ts"] >= runner.journal.records("DECISION").__next__()["ts"], True)
    pos = runner.engine.position
    st.check("Position_Ziel", CONTRACT.ticks_to_price(pos.target_ticks), Decimal("4995.50"))
    st.check("Zustand", res.final_state, "OPEN")


def test_T11_widerlegung_zuerst(selftest):
    st = selftest("T11", "Widerlegung zuerst", "10.2")
    s = base_session()
    add_breakout(s)
    # Folgebalken: zuerst Trade an 5001,50, später Schluss bei 4999,50.
    s.bar(s.at("10:16"), [STOP, P + 2, P, CONFIRM_LEVEL], 60, 60)
    s.quiet_bars(s.at("10:17"), 8, P - 14, P - 10)
    runner, res = run(s, until=s.at("10:25:30"))
    rel = non_routine(res)
    st.check("Verwerfungsgrund", [d["reason_codes"] for d in rel if R.CANDIDATE_REJECTED_BARRIER in d["reason_codes"]],
             [[R.CANDIDATE_REJECTED_BARRIER]])
    st.check("kein_Short", [d for d in res.decisions if d["action"] == "PROPOSE_SHORT"], [])
    st.check("keine_Order", len(_orders(runner)), 0)
    after_close = [d for d in res.decisions if d["decided_at"] == "2026-09-22T14:17:00.100Z"]
    st.check("Schlusskurs_löscht_Widerlegung_nicht", after_close[0]["reason_codes"], [R.COOLDOWN])
    # Der angebrochene Balken 10:16 zählt nicht; gewartet wird 5 vollständige Balken (10:17–10:21).
    counted = [d["decided_at"][11:19] for d in res.decisions
               if d["reason_codes"] == [R.COOLDOWN] and d["decided_at"] > "2026-09-22T14:17:00.100Z"]
    st.check("Wartezeit_vollständige_Balken", counted, ["14:18:00", "14:19:00", "14:20:00", "14:21:00", "14:22:00"])
    nxt = [d for d in res.decisions if d["decided_at"] == "2026-09-22T14:23:00.100Z"][0]
    st.check("frühestens_danach_neue_Suche", (nxt["state"], nxt["reason_codes"]), ("IDLE", [R.NO_SIGNAL]))


def test_T12_fristablauf(selftest):
    st = selftest("T12", "Fristablauf", "10.2")
    s = base_session()
    add_breakout(s)
    s.bar(s.at("10:16"), [P + 1, P], 60, 60)                    # Schluss 5000,00
    s.bar(s.at("10:17"), [P, P + 1], 60, 60)                    # Schluss 5000,25
    s.bar(s.at("10:18"), [P, CONFIRM_LEVEL + 1], 60, 60)        # Schluss 4999,75
    s.bar(s.at("10:19"), [P - 1, CONFIRM_LEVEL], 60, 60)        # Schluss 4999,50 erst in Balken 4
    runner, res = run(s, until=s.at("10:20:30"))
    by_time = {d["decided_at"]: d for d in res.decisions}
    st.check("Balken1", by_time["2026-09-22T14:17:00.100Z"]["reason_codes"], [R.WAIT_CONFIRMATION])
    st.check("Balken2", by_time["2026-09-22T14:18:00.100Z"]["reason_codes"], [R.WAIT_CONFIRMATION])
    st.check("Verwerfung_nach_Balken3", by_time["2026-09-22T14:19:00.100Z"]["reason_codes"],
             [R.CANDIDATE_REJECTED_EXPIRY])
    st.check("Balken4_gehört_nicht_zum_Kandidaten", by_time["2026-09-22T14:20:00.100Z"]["reason_codes"], [R.COOLDOWN])
    st.check("kein_Short", [d for d in res.decisions if d["action"] == "PROPOSE_SHORT"], [])


def test_T13_veraltete_quote(selftest):
    st = selftest("T13", "Veraltete Quote", "10.2")
    # Variante a: Quote am Eintritt 400 ms alt, spätere gültige Quote innerhalb der Signalgültigkeit.
    s = base_session()
    add_breakout(s)
    s.bar(s.at("10:16"), [P + 1, P, P - 1, CONFIRM_LEVEL], 60, 60)
    s.no_heartbeat(s.at("10:16:59.500"), s.at("10:17:03"))
    s.quote(s.at("10:16:59.930"), CONFIRM_LEVEL, CONFIRM_LEVEL + 1)       # empfangen .950
    s.quote(s.at("10:17:01.000"), CONFIRM_LEVEL, CONFIRM_LEVEL + 1)       # empfangen 01.020
    runner, res = run(s, until=s.at("10:17:10"))
    statuses = [r["payload"] for r in runner.journal.records("ORDER_STATUS")]
    fills = [r["payload"] for r in runner.journal.records("FILL")]
    st.check("Quotealter_am_ersten_Eintritt_ms", 400, 400)
    st.check("kein_Fill_um_14:17:00.350", [f for f in fills if f["ts"] <= s.at("10:17:00.350")], [])
    st.check("Fill_mit_späterer_gültiger_Quote", [format_ts(f["ts"]) for f in fills], ["2026-09-22T14:17:01.270Z"])
    st.true("Fill_innerhalb_Signalgültigkeit", fills[0]["ts"] <= s.at("10:17:00.100") + 2_000_000)
    st.check("Quotealter_beim_Fill_us", fills[0]["quote_age_us"], 250_000)
    st.check("Orderstatus", [x["status"] for x in statuses], ["WORKING"])

    # Variante b: nächste gültige Quote erst nach Ablauf der Signalgültigkeit -> kein Eintritt.
    s2 = base_session(tag="B")
    add_breakout(s2)
    s2.bar(s2.at("10:16"), [P + 1, P, P - 1, CONFIRM_LEVEL], 60, 60)
    s2.no_heartbeat(s2.at("10:16:59.500"), s2.at("10:17:03"))
    s2.quote(s2.at("10:16:59.930"), CONFIRM_LEVEL, CONFIRM_LEVEL + 1)
    s2.quote(s2.at("10:17:02.500"), CONFIRM_LEVEL, CONFIRM_LEVEL + 1)
    runner2, res2 = run(s2, until=s2.at("10:17:10"))
    st.check("Variante_b_Fills", list(runner2.journal.records("FILL")), [])
    st.check("Variante_b_Entscheidung", res2.decisions[-1]["reason_codes"], [R.NO_TRADE_EXPIRED])
    exp = [r["payload"] for r in runner2.journal.records("ORDER_STATUS") if r["payload"]["status"] == "EXPIRED"]
    st.check("Variante_b_Ablaufgründe", exp[0]["reasons"], ["SIGNAL_EXPIRED", "QUOTE_STALE"])


def test_T14_zu_spaete_ki_antwort(selftest):
    st = selftest("T14", "Zu späte KI-Antwort", "10.2")
    s = _confirm_scenario()
    s.quiet_bars(s.at("10:17"), 3, P - 14, P - 10)
    runner, res = run(s, entry_filter=EntryFilterModel(latency_ms=3000, approve=True), until=s.at("10:19:30"))
    rel = non_routine(res)
    st.check("Antwort_nach_3s", [d["decided_at"] for d in rel if R.NO_TRADE_EXPIRED in d["reason_codes"]],
             ["2026-09-22T14:17:03.100Z"])
    st.check("Entscheidung", [d["reason_codes"] for d in rel if R.NO_TRADE_EXPIRED in d["reason_codes"]],
             [[R.NO_TRADE_EXPIRED]])
    st.check("kein_Vorschlag", [d for d in res.decisions if d["action"] == "PROPOSE_SHORT"], [])
    st.check("kein_rückwirkender_Fill", list(runner.journal.records("FILL")), [])
    st.check("keine_Order", len(_orders(runner)), 0)
    # Gegenprobe: rechtzeitige Antwort (1 s) führt zum Vorschlag mit verbleibender Gültigkeit.
    s2 = _confirm_scenario(tag="B")
    s2.quote(s2.at("10:17:01.050"), CONFIRM_LEVEL, CONFIRM_LEVEL + 1)
    runner2, res2 = run(s2, entry_filter=EntryFilterModel(latency_ms=1000), until=s2.at("10:17:10"))
    prop = [d for d in res2.decisions if d["action"] == "PROPOSE_SHORT"]
    st.check("Gegenprobe_rechtzeitig_gültig_bis", prop[0]["proposed_order"]["valid_until"], "2026-09-22T14:17:02.100Z")


def test_T02_im_regelmodul(selftest):
    st = selftest("T02", "Unbekannte Seite im Regelmodul (Ausbruchsbalken mit U = 70)", "10.1/7.2")
    s = base_session()
    s.bar(s.at("10:15"), [L, P + 1, H, H - 1, P + 2, P + 2], 140, 90, 70)
    runner, res = run(s, until=s.at("10:16:00.100"))
    d = res.decisions[-1]
    st.check("Aktion", d["action"], "NO_TRADE")
    st.check("Gründe", d["reason_codes"], [R.NO_TRADE_DATA_QUALITY])
    st.check("K", d["facts"]["known_fraction"], 230 / 300)
    st.check("Zustand", res.final_state, "IDLE")
