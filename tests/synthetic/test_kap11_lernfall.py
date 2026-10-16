"""Handbuch Kap. 11: durchgearbeiteter blinder Lernfall mit zwei Fortsetzungen."""
from decimal import Decimal

from tests.scenarios import CONFIRM_LEVEL, CONTRACT, P, STOP, add_breakout, base_session, run

M = CONTRACT.price_to_ticks


def _session(continuation: str):
    s = base_session(tag="K11")  # dieselbe Sitzung, zwei mögliche Fortsetzungen
    add_breakout(s)
    s.bar(s.at("10:16"), [P + 1, P, P - 1, CONFIRM_LEVEL], 60, 60)
    s.quote(s.at("10:17:00.300"), CONFIRM_LEVEL, CONFIRM_LEVEL + 1)
    if continuation == "A":
        s.bar(s.at("10:17"), [M("4999.00"), M("4998.00"), M("4997.00"), M("4995.50")], 0, 120)
    else:
        s.bar(s.at("10:17"), [M("5000.00"), M("5001.00"), STOP], 120, 0)
        stop_trade = max(t.ts for t in s.trades if t.price == STOP)
        s.quote(stop_trade + 100_000, STOP, M("5001.75"))
    s.quiet_bars(s.at("10:18"), 3, P - 20, P - 16)
    return s


def test_kap11_zwei_fortsetzungen(selftest):
    st = selftest("K11", "Blinder Lernfall Phase 1–4", "11")
    ra, a = run(_session("A"), until=_session("A").at("10:21"))
    rb, b = run(_session("B"), until=_session("B").at("10:21"))
    fill_ts = next(ra.journal.records("FILL"))["ts"]
    before_a = [d for d in a.decisions if d["decided_at"] <= "2026-09-22T14:17:00.350Z"]
    before_b = [d for d in b.decisions if d["decided_at"] <= "2026-09-22T14:17:00.350Z"]
    st.check("Entscheidungen_bis_Entry_identisch", before_a == before_b and len(before_a) > 0, True)
    st.check("Entry_Fill", CONTRACT.ticks_to_price(next(ra.journal.records("FILL"))["payload"]["price_ticks"]),
             Decimal("4999.50"))
    st.check("Fill_vor_Folgeereignissen", fill_ts < _session("A").at("10:17:12"), True)
    ta, tb = a.results[0], b.results[0]
    st.check("A_Exit", (ta["exit_reason"], CONTRACT.ticks_to_price(ta["exit_ticks"])), ("TARGET", Decimal("4995.75")))
    st.check("A_brutto", Decimal(ta["gross_usd"]), Decimal("18.75"))
    st.check("A_netto", Decimal(ta["net_usd"]), Decimal("16.25"))
    st.check("A_R", Decimal(ta["r_usd"]), Decimal("10.00"))
    st.check("A_netto_R", Decimal(ta["net_r"]), Decimal("1.625"))
    st.check("B_Exit", (tb["exit_reason"], CONTRACT.ticks_to_price(tb["exit_ticks"])), ("STOP", Decimal("5001.75")))
    st.check("B_brutto", Decimal(tb["gross_usd"]), Decimal("-11.25"))
    st.check("B_netto", Decimal(tb["net_usd"]), Decimal("-13.75"))
    st.check("B_netto_R", Decimal(tb["net_r"]), Decimal("-1.375"))
    st.check("beide_regelkonform_bewertet", (ta["outcome"], tb["outcome"]), ("SCORED", "SCORED"))
