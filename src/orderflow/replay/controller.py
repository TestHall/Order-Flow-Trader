"""Replay-Controller (Kap. 2, 5.2, 16.2): gibt Ereignisse strikt in Empfangsreihenfolge frei.

Ablauf je Zeitpunkt: Ereignisse -> Snapshot -> Entscheidung (protokolliert) -> erst danach weitere
Ereignisse. Der Controller kennt keine Prüferlösungen und importiert den Prüfer nicht.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from orderflow.common.config import Policy, SimConfig
from orderflow.common.contracts import ExitIntent
from orderflow.common.scheduler import PRIO_BAR, PRIO_MARKET, PRIO_TIMER, Scheduler
from orderflow.common.timeutil import MS, SECOND, format_ts
from orderflow.common.units import ContractSpec
from orderflow.dataadapter.events import MarketEvent, knowledge_order
from orderflow.dataadapter.knowledge import Knowledge
from orderflow.dataadapter.quality import StreamChecker, Verdict, quote_flags
from orderflow.execution.orders import OrderManager
from orderflow.execution.simulator import ExecutionSimulator
from orderflow.features.bars import BarBuilder
from orderflow.features.market import MarketView
from orderflow.features.session import SessionContext
from orderflow.journal.journal import Journal
from orderflow.rules import reasons as R
from orderflow.rules.engine import EntryFilterModel, PolicyEngine


@dataclass
class SessionResult:
    session_id: str
    decisions: list[dict[str, Any]]
    results: list[dict[str, Any]]
    trace: list[tuple[int, str, str, str]]
    quality: list[dict[str, Any]]
    late_events: list[str]
    final_state: str
    halt_reasons: list[str]
    journal_head: str
    counters: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        actions: dict[str, int] = {}
        for d in self.decisions:
            actions[d["action"]] = actions.get(d["action"], 0) + 1
        return {
            "session_id": self.session_id,
            "final_state": self.final_state,
            "decisions": len(self.decisions),
            "actions": actions,
            "trades": len(self.results),
            "scored_trades": sum(1 for r in self.results if r["scorable"]),
            "unscorable_trades": sum(1 for r in self.results if not r["scorable"]),
            "net_usd_scored": str(sum((Decimal(r["net_usd"]) for r in self.results if r["scorable"]),
                                      Decimal(0))),
            "quality_issues": len(self.quality),
            "late_events": len(self.late_events),
            "journal_head": self.journal_head,
            **self.counters,
        }


class SessionRunner:
    def __init__(self, events: list[MarketEvent], ctx: SessionContext, policy: Policy, sim: SimConfig,
                 contract: ContractSpec, journal: Journal, mode: str = "REPLAY",
                 entry_filter: EntryFilterModel | None = None,
                 on_step: Callable[["SessionRunner", str], None] | None = None,
                 resume_at: int | None = None):
        self.ctx = ctx
        self.policy = policy
        self.sim = sim
        self.journal = journal
        self.resume_at = resume_at
        self.scheduler = Scheduler()
        self.market = MarketView()
        self.knowledge = Knowledge()
        self.checker = StreamChecker()
        self.bars = BarBuilder(ctx.window_start, ctx.window_end, policy.bar_seconds * SECOND,
                               sim.bar_finalization_ms * MS)
        self.orders = OrderManager.rebuild(journal) if resume_at is not None else OrderManager(journal)
        self.execution = ExecutionSimulator(self.scheduler, self.market, self.orders, policy, sim, contract)
        self.engine = PolicyEngine(policy, sim, contract, ctx, self.scheduler, self.market, self.knowledge,
                                   journal, mode=mode, entry_filter=entry_filter)
        self.engine.execution = self.execution
        self.execution.listener = self.engine
        if on_step is not None:
            hook = lambda label: on_step(self, label)  # noqa: E731
            self.engine.on_step = hook
            self.execution.on_step = hook
        self.halt_intervals: list[list[int | None]] = []
        self.quality: list[dict[str, Any]] = []
        self.counters = {"events_in": 0, "duplicates_dropped": 0, "book_events": 0, "corrections": 0}
        self._events = knowledge_order(events)
        self._started = False

    # ----------------------------------------------------------- Aufbau
    def _start(self) -> None:
        self._started = True
        start = self.resume_at
        if start is None:
            self.journal.append("SESSION_START", self.ctx.window_start, {
                "session_id": self.ctx.session_id, "instrument_id": self.ctx.instrument_id,
                "day": self.ctx.day.isoformat(), "window": [format_ts(self.ctx.window_start),
                                                            format_ts(self.ctx.window_end)],
                "prior_high_ticks": self.ctx.prior_high_ticks, "evaluable": self.ctx.evaluable,
                "policy_version": self.policy.policy_version, "policy_hash": self.policy.policy_hash,
                "simulation_version": self.sim.simulation_version, "simulation_hash": self.sim.config_hash,
                "mode": self.engine.mode,
            })
            events = self._events
        else:
            self.scheduler.now = start
            self.journal.append("RESTART", start, {"session_id": self.ctx.session_id})
            events = [e for e in self._events if e.receive_ts > start]
            for bar in self.bars.bars:
                if bar.available_at <= start:
                    bar.sealed = True
                elif bar.start < start:
                    bar.complete = False
                    bar.flags.add("RESTART")
            self._restore(start)
        for e in events:
            self.scheduler.at(e.receive_ts, PRIO_MARKET, lambda t, e=e: self._on_event(e, t))
        for bar in self.bars.bars:
            if not bar.sealed:
                self.scheduler.at(bar.available_at, PRIO_BAR, lambda t, i=bar.index: self._on_seal(i, t))
        self.scheduler.at(max(self.ctx.window_end, self.scheduler.now), PRIO_TIMER, self._on_session_end)

    def _restore(self, t: int) -> None:
        entry_fills, exit_fills, working_entry, working_exit, meta = [], [], [], [], {}
        for o in self.orders.orders.values():
            if o.kind == "ENTRY":
                meta[o.client_order_id] = {"candidate_number": o.meta["candidate_number"],
                                           "stop_ticks": o.meta["stop_ticks"]}
        for f in self.orders.fills:
            (entry_fills if self.orders.orders[f.client_order_id].kind == "ENTRY" else exit_fills).append(f)
        for o in self.orders.working():
            if o.kind == "ENTRY":
                # Status der gesendeten Order beim Absturz unbekannt -> konservativ storniert.
                self.orders.set_status(o.client_order_id, "CANCELLED", t, [R.RESTART_RECOVERY])
            else:
                working_exit.append(o.client_order_id)
        self.engine.restore(t, entry_fills, exit_fills, working_entry, working_exit, meta)
        for cid in working_exit:
            o = self.orders.orders[cid]
            self.execution.resume_exit(ExitIntent(cid, o.side, o.quantity, o.meta.get("reason", "UNKNOWN"),
                                                  t, o.meta.get("trigger_ref", "restart")), t)

    # -------------------------------------------------------- Ereignisse
    def _on_event(self, e: MarketEvent, t: int) -> None:
        self.counters["events_in"] += 1
        verdict = self.checker.check(e)
        if verdict == Verdict.DUPLICATE:
            self.counters["duplicates_dropped"] += 1
            self.quality.append({"t": format_ts(t), "kind": "DUPLICATE", "stream": e.stream_id, "seq": e.sequence})
            return
        if verdict in (Verdict.CONFLICTING_DUPLICATE, Verdict.OUT_OF_ORDER):
            self.quality.append({"t": format_ts(t), "kind": verdict.value, "stream": e.stream_id, "seq": e.sequence})
            self.journal.append("QUALITY", t, self.quality[-1])
            self.engine.on_data_problem(R.DATA_GAP, t, f"{verdict.value} {e.stream_id}#{e.sequence}")
            return
        if verdict == Verdict.GAP:
            issue = self.checker.issues[-1]
            self.bars.mark_incomplete(issue.gap_from_exchange_ts, issue.gap_to_exchange_ts, "SEQUENCE_GAP")
            self.quality.append({"t": format_ts(t), "kind": "GAP", "stream": e.stream_id, "detail": issue.detail})
            self.journal.append("QUALITY", t, self.quality[-1])
            self.engine.on_data_problem(R.DATA_GAP, t, f"{e.stream_id}: {issue.detail}")
        self.knowledge.add(e)
        if e.event_type == "TRADE":
            self.bars.add_trade(e)
            self.market.on_trade(e)
            self.engine.on_trade(e, t)
        elif e.event_type == "QUOTE":
            flags = quote_flags(e)
            if flags:
                self.quality.append({"t": format_ts(t), "kind": "QUOTE", "flags": flags, "event": e.event_id})
            self.market.on_quote(e)
            self.execution.on_quote(t)
        elif e.event_type == "STATUS":
            self._on_status(e, t)
        elif e.event_type in ("TRADE_CORRECTION", "TRADE_BUST"):
            self.counters["corrections"] += 1
            i = self.bars.index_of(e.exchange_ts)
            if i is not None and not self.bars.bars[i].sealed:
                self.bars.mark_incomplete(e.exchange_ts, e.exchange_ts, e.event_type)
            else:
                self.quality.append({"t": format_ts(t), "kind": "LATE_" + e.event_type, "event": e.event_id})
        elif e.event_type == "BOOK":
            self.counters["book_events"] += 1  # OF-A-1.0 nutzt keine Tiefe

    def _on_status(self, e: MarketEvent, t: int) -> None:
        if e.market_status in ("HALT", "CLOSED"):
            if not self.halt_intervals or self.halt_intervals[-1][1] is not None:
                self.halt_intervals.append([e.exchange_ts, None])
            self.engine.on_data_problem(R.TRADING_HALT, t, e.market_status)
        elif e.market_status == "OPEN" and self.halt_intervals and self.halt_intervals[-1][1] is None:
            self.halt_intervals[-1][1] = e.exchange_ts
            self.engine.on_market_open(t)

    def _on_seal(self, index: int, t: int) -> None:
        bar = self.bars.seal(index)
        for hs, he in self.halt_intervals:
            if hs < bar.end and (he is None or he > bar.start):
                bar.complete = False
                bar.flags.add("TRADING_HALT")
        self.engine.on_bar(bar, t)

    def _on_session_end(self, t: int) -> None:
        self.engine.finish(t)

    # -------------------------------------------------------------- Lauf
    def run(self, until: int | None = None) -> SessionResult:
        if not self._started:
            self._start()
        self.scheduler.run(until)
        return self.result()

    def result(self) -> SessionResult:
        return SessionResult(
            session_id=self.ctx.session_id,
            decisions=list(self.engine.decisions),
            results=list(self.engine.results),
            trace=list(self.engine.trace),
            quality=list(self.quality),
            late_events=[e.event_id for e in self.bars.late_events],
            final_state=self.engine.state.value,
            halt_reasons=sorted(self.engine.halt_reasons),
            journal_head=self.journal.head,
            counters=dict(self.counters),
        )

    def state_view(self) -> dict[str, Any]:
        """Zustand für den Invariantenprüfer (Kap. 7.5)."""
        return {
            "engine": self.engine.core_state(),
            "orders": [{"client_order_id": o.client_order_id, "kind": o.kind, "side": o.side,
                        "quantity": o.quantity, "status": o.status, "created_at": o.created_at}
                       for o in self.orders.orders.values()],
            "fills": [f.to_record() for f in self.orders.fills],
            "position_from_fills": self.orders.position(),
        }
