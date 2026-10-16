"""Gemeinsame synthetische Szenarien für Kap. 10.2 / 11 (P = 5000,00, H = 5001,00, L = 4999,75)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from orderflow.common.config import SimConfig, load_policy, load_sim_config
from orderflow.common.units import load_contract
from orderflow.journal.journal import Journal
from orderflow.replay.controller import SessionResult, SessionRunner
from orderflow.replay.synthetic import SyntheticSession
from orderflow.rules.engine import EntryFilterModel
from orderflow.verifier.invariants import InvariantMonitor

POLICY = load_policy()
SIM = load_sim_config()
CONTRACT = load_contract("MES")
DAY = date(2026, 9, 22)

P = CONTRACT.price_to_ticks("5000.00")
H = CONTRACT.price_to_ticks("5001.00")
L = CONTRACT.price_to_ticks("4999.75")
CONFIRM_LEVEL = CONTRACT.price_to_ticks("4999.50")
STOP = CONTRACT.price_to_ticks("5001.50")


def base_session(tag: str = "A", **kw) -> SyntheticSession:
    """45 ruhige Balken 09:30–10:14 unter P (Volumen je 120, Median 120), B = S."""
    s = SyntheticSession(DAY, POLICY, tag=tag, **kw)
    s.quiet_bars(s.at("09:30"), 45, P - 8, P - 4)
    return s


def add_breakout(s: SyntheticSession, minute: str = "10:15") -> None:
    """Ausbruchsbalken: Tief 4999,75, Hoch 5001,00, Schluss 5000,50, B = 140, S = 90, U = 0."""
    prices = [L, P + 1, H, H - 1, P + 2, P + 2]
    s.bar(s.at(minute), prices, 140, 90)


def run(s: SyntheticSession, prior_high: int | None = P, journal: Journal | None = None,
        sim: SimConfig = SIM, entry_filter: EntryFilterModel | None = None, until: int | None = None,
        monitor: InvariantMonitor | None = None, journal_path: Path | None = None,
        **ctx_kw) -> tuple[SessionRunner, SessionResult]:
    if journal is None:
        journal = Journal(journal_path)
    mon = monitor if monitor is not None else InvariantMonitor()
    runner = SessionRunner(s.events(sim), s.context(prior_high, **ctx_kw), POLICY, sim, CONTRACT, journal,
                           entry_filter=entry_filter, on_step=mon)
    return runner, runner.run(until=until)


def actions(result: SessionResult) -> list[tuple[str, str, tuple[str, ...]]]:
    return [(d["decided_at"], d["action"], tuple(d["reason_codes"])) for d in result.decisions]


def non_routine(result: SessionResult) -> list[dict]:
    routine = {("NO_TRADE", ("OUTSIDE_SIGNAL_TIME",)), ("NO_TRADE", ("NO_SIGNAL",)), ("NO_TRADE", ("WARMUP",))}
    return [d for d in result.decisions if (d["action"], tuple(d["reason_codes"])) not in routine]
