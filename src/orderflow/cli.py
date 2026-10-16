"""Betreiber-Kommandozeile.

Liegt bewusst außerhalb der Komponentenpakete: der Betreiber startet den Replay (Kandidat) und
danach getrennt den Prüfer. Der Replay selbst importiert den Prüfer nie.

  orderflow selftest [--report reports/m1/selftest_results.json]
  orderflow replay-synthetic --seeds 1-20 --out reports/m2/synthetic
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from orderflow.common.config import load_policy, load_sim_config
from orderflow.common.paths import REPO_ROOT
from orderflow.common.timeutil import MS
from orderflow.common.units import load_contract
from orderflow.journal.journal import Journal
from orderflow.replay.controller import SessionRunner
from orderflow.replay.synthetic import random_session
from orderflow.verifier.fills import verify_fills
from orderflow.verifier.invariants import InvariantMonitor
from orderflow.verifier.timing import verify_snapshots

DISCLAIMER = ("Synthetische Sitzungen aus orderflow.replay.synthetic.random_session. Keine echten Marktdaten; "
              "PnL-Zahlen sind technische Kontrollgrößen ohne jede Aussage über Marktleistung (Kap. 1, 16.3). "
              "Synthetische Quotes ändern sich nur bei Trades; Latenzstress verschiebt daher kaum Fill-Preise.")


def _seeds(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def _run_session(seed: int, sim, journal_path: Path | None) -> dict[str, Any]:
    from datetime import date

    policy, contract = load_policy(), load_contract()
    s = random_session(date(2026, 9, 22), policy, seed)
    events = s.events(sim)
    monitor = InvariantMonitor(policy.max_contracts, policy.max_filled_entries)
    if journal_path is not None and journal_path.exists():
        journal_path.unlink()
    runner = SessionRunner(events, s.context(20000), policy, sim, contract, Journal(journal_path), on_step=monitor)
    res = runner.run()
    recs = list(runner.journal.records())
    fills = verify_fills(recs, events, sim.order_latency_ms * MS, sim.exit_latency_ms * MS)
    snaps = verify_snapshots(recs, events)
    candidates = sum(1 for t in res.trace if t[2] == "ARMED")
    return {
        **res.summary(),
        "seed": seed,
        "candidates": candidates,
        "events": len(events),
        "invariant_checks": monitor.checks,
        "fill_violations": fills,
        "time_violations": snaps,
        "results": res.results,
    }


def cmd_replay_synthetic(args: argparse.Namespace) -> int:
    base = load_sim_config()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    seeds = _seeds(args.seeds)
    sessions = []
    for seed in seeds:
        first = _run_session(seed, base, out / f"journal_seed{seed:03d}.jsonl" if args.keep_journals else None)
        second = _run_session(seed, base, None)
        first["reproducible"] = first["journal_head"] == second["journal_head"]
        sessions.append(first)
        print(f"seed {seed:3d}: {first['final_state']:17s} Kandidaten={first['candidates']} "
              f"Trades={first['trades']} reproduzierbar={first['reproducible']}")
    stress = []
    for slip in (0, 1, 2):
        for lat in (250, 500, 1000):
            sim = replace(base, extra_slippage_ticks=slip, order_latency_ms=lat, exit_latency_ms=lat)
            runs = [_run_session(seed, sim, None) for seed in seeds]
            stress.append({
                "extra_slippage_ticks": slip, "latency_ms": lat,
                "trades": sum(r["trades"] for r in runs),
                "unscorable": sum(r["unscorable_trades"] for r in runs),
                "net_usd_scored": str(sum(Decimal(r["net_usd_scored"]) for r in runs)),
                "fill_violations": sum(len(r["fill_violations"]) for r in runs),
                "time_violations": sum(len(r["time_violations"]) for r in runs),
            })
    report = {
        "report": "M2 – vollständige synthetische Sitzungen, deterministisch und reproduzierbar (Kap. 16.3 Schritt 3)",
        "statement": DISCLAIMER,
        "policy_version": load_policy().policy_version,
        "policy_sha256": load_policy().policy_hash,
        "simulation_version": base.simulation_version,
        "simulation_sha256": base.config_hash,
        "totals": {
            "sessions": len(sessions),
            "reproducible_sessions": sum(s["reproducible"] for s in sessions),
            "candidates": sum(s["candidates"] for s in sessions),
            "decisions": sum(s["decisions"] for s in sessions),
            "trades": sum(s["trades"] for s in sessions),
            "unscorable_trades": sum(s["unscorable_trades"] for s in sessions),
            "no_trade_sessions": sum(1 for s in sessions if s["trades"] == 0),
            "sessions_with_quality_issues": sum(1 for s in sessions if s["quality_issues"]),
            "invariant_checks": sum(s["invariant_checks"] for s in sessions),
            "fill_violations": sum(len(s["fill_violations"]) for s in sessions),
            "time_violations": sum(len(s["time_violations"]) for s in sessions),
        },
        "stress_scenarios": stress,
        "sessions": sessions,
    }
    (out / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    t = report["totals"]
    print(json.dumps(t, indent=2))
    ok = t["reproducible_sessions"] == t["sessions"] and not t["fill_violations"] and not t["time_violations"]
    return 0 if ok else 1


def cmd_selftest(args: argparse.Namespace) -> int:
    cmd = [sys.executable, "-m", "pytest", str(REPO_ROOT / "tests"), "-q", f"--selftest-report={args.report}"]
    return subprocess.call(cmd, cwd=REPO_ROOT)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="orderflow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("selftest", help="T01–T20 und Invarianten; schreibt PASS/FAIL-Bericht")
    a.add_argument("--report", default=str(REPO_ROOT / "reports" / "m1" / "selftest_results.json"))
    a.set_defaults(fn=cmd_selftest)
    b = sub.add_parser("replay-synthetic", help="Vollständige synthetische Sitzungen + Prüfung")
    b.add_argument("--seeds", default="1-20")
    b.add_argument("--out", default=str(REPO_ROOT / "reports" / "m2" / "synthetic"))
    b.add_argument("--keep-journals", action="store_true")
    b.set_defaults(fn=cmd_replay_synthetic)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
