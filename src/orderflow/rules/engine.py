"""Zustandsautomat OF-A-1.0 (Handbuch Kap. 7.2–7.5).

Das Regelmodul sieht nur, was ihm der Replay-Controller bis zum aktuellen Empfangszeitpunkt
übergibt: versiegelte Balken, empfangene Trades, den zuletzt empfangenen Quote-Stand und
Qualitätsmeldungen. Es erzeugt Entscheidungen (vor Kenntnis des Ergebnisses protokolliert) und
Absichten an das Ausführungsmodul. Fills entstehen ausschließlich dort.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol

from orderflow.common import schema
from orderflow.common.config import Policy, SimConfig
from orderflow.common.contracts import EntryIntent, ExitIntent, Fill
from orderflow.common.scheduler import PRIO_TIMER, Scheduler
from orderflow.common.timeutil import MINUTE, MS, SECOND, format_ts, local_to_us
from orderflow.common.units import ContractSpec
from orderflow.dataadapter.events import MarketEvent
from orderflow.dataadapter.knowledge import Knowledge
from orderflow.execution.costs import short_trade_pnl
from orderflow.execution.risk import position_size, short_levels
from orderflow.features import FEATURE_VERSION
from orderflow.features.bars import Bar
from orderflow.features.core import median
from orderflow.features.market import MarketView
from orderflow.features.session import SessionContext
from orderflow.journal.journal import Journal
from orderflow.rules import reasons as R
from orderflow.rules.states import State


class ExecutionPort(Protocol):
    def submit_entry(self, intent: EntryIntent, t: int) -> None: ...
    def cancel_entry(self, client_order_id: str, t: int) -> None: ...
    def submit_exit(self, intent: ExitIntent, t: int) -> None: ...


@dataclass(frozen=True)
class EntryFilterModel:
    """Optionaler KI-Filter als eigener Kandidat (Kap. 2.1, M5). Für Tests mit fester Antwortzeit.

    Die Antwort gilt erst zum Zeitpunkt Bestätigung + latency_ms; eine zu späte Antwort führt zu
    NO_TRADE_EXPIRED und wird nie rückwirkend zum ursprünglichen Preis ausgeführt."""
    latency_ms: int
    approve: bool = True


@dataclass
class Candidate:
    number: int
    prior_high: int
    high: int
    low: int
    breakout_end: int
    armed_at: int
    confirm_level: int
    barrier: int
    stop: int
    bars_seen: int = 0
    confirmed_at: int | None = None
    entry_client_id: str | None = None


@dataclass
class Position:
    candidate_number: int
    quantity: int
    entry_ticks: int
    entry_ts: int
    stop_ticks: int
    distance_ticks: int
    target_ticks: int
    time_exit_at: int
    entry_client_id: str
    exit_client_id: str
    unscorable: list[str] = field(default_factory=list)
    exit_reason: str | None = None
    risk_overrun: bool = False


class PolicyEngine:
    def __init__(self, policy: Policy, sim: SimConfig, contract: ContractSpec, ctx: SessionContext,
                 scheduler: Scheduler, market: MarketView, knowledge: Knowledge, journal: Journal,
                 mode: str = "REPLAY", entry_filter: EntryFilterModel | None = None):
        if mode not in ("REPLAY", "SHADOW"):
            raise ValueError("Nur REPLAY oder SHADOW; echte Orders sind nicht vorgesehen (Kap. 1.2)")
        self.p = policy
        self.sim = sim
        self.contract = contract
        self.ctx = ctx
        self.scheduler = scheduler
        self.market = market
        self.knowledge = knowledge
        self.journal = journal
        self.mode = mode
        self.entry_filter = entry_filter
        self.execution: ExecutionPort | None = None
        self.on_step: Callable[[str], None] | None = None

        tz = policy.timezone
        self.signal_start = local_to_us(ctx.day, policy.signal_start, tz)
        self.signal_end = local_to_us(ctx.day, policy.signal_end_exclusive, tz)
        self.entry_cutoff = min(local_to_us(ctx.day, policy.latest_exit_local, tz), ctx.window_end)

        self.state = State.IDLE
        self.halt_reasons: set[str] = set()
        self.bars: list[Bar] = []
        self.contiguous_complete = 0
        self.candidate: Candidate | None = None
        self.candidates_count = 0
        self.entries_filled = 0
        self.cooldown_remaining = 0
        self.cooldown_since: int | None = None
        self.position: Position | None = None
        self.entry_meta: dict[str, dict[str, int]] = {}
        self.decisions: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []
        self.trace: list[tuple[int, str, str, str]] = []
        self.decision_counter = 0

    # ------------------------------------------------------------------ Hilfen
    def _cid(self, n: int, kind: str) -> str:
        return f"{self.ctx.session_id}-C{n}-{kind}"

    def _set_state(self, new: State, t: int, why: str) -> None:
        if new != self.state:
            self.trace.append((t, self.state.value, new.value, why))
            self.state = new
            self.journal.append("STATE", t, self.core_state())
        self._step(f"state:{new.value}")

    def _step(self, label: str) -> None:
        if self.on_step is not None:
            self.on_step(label)

    def core_state(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "halt_new_entries": bool(self.halt_reasons),
            "halt_reasons": sorted(self.halt_reasons),
            "candidates_count": self.candidates_count,
            "entries_filled": self.entries_filled,
            "cooldown_remaining": self.cooldown_remaining,
            "candidate": asdict(self.candidate) if self.candidate else None,
            "position": asdict(self.position) if self.position else None,
            "decision_counter": self.decision_counter,
        }

    def _previous_close(self) -> int | None:
        for b in reversed(self.bars[:-1]):
            if b.close is not None:
                return b.close
        return None

    def _reference_volumes(self) -> list[int]:
        ref = self.bars[-1 - self.p.reference_bars:-1]
        return [b.volume for b in ref]

    def _news_hits(self, start: int, end: int, t: int) -> list[str]:
        if self.ctx.news is None:
            return []
        return [n.label for n in self.ctx.news.blackout(start, end, t, self.p.news_before_minutes,
                                                         self.p.news_after_minutes)]

    # ------------------------------------------------------- Entscheidungen
    def _snapshot(self, t: int, bar: Bar | None, extra: dict[str, Any], valid_until: int) -> dict[str, Any]:
        flags = set()
        if bar is not None:
            flags |= set(bar.flags) | set(bar.stats.flags)
            if not bar.complete:
                flags.add("BAR_INCOMPLETE")
        flags |= {f"HALT:{h}" for h in self.halt_reasons}
        if not self.ctx.evaluable:
            status = "INVALID"
        elif flags:
            status = "DEGRADED"
        else:
            status = "OK"
        q = self.market.quote
        features = {
            "bar": bar.summary() if bar is not None else None,
            "prior_high_ticks": self.ctx.prior_high_ticks,
            "state": self.state.value,
            "contiguous_complete_bars": self.contiguous_complete,
            "quote": None if q is None else {
                "bid_ticks": q.bid_ticks, "ask_ticks": q.ask_ticks, "bid_size": q.bid_size,
                "ask_size": q.ask_size, "age_ms": (t - q.receive_ts) / MS},
            **extra,
        }
        snap = {
            "snapshot_id": f"{self.ctx.session_id}-S{self.decision_counter:04d}",
            "as_of_receive_ts": format_ts(t),
            "max_exchange_ts_seen": None if self.knowledge.max_exchange_ts is None
            else format_ts(self.knowledge.max_exchange_ts),
            "event_count": self.knowledge.count,
            "events_digest": self.knowledge.digest,
            "instrument_id": self.ctx.instrument_id,
            "session_id": self.ctx.session_id,
            "quality_status": status,
            "quality_flags": sorted(flags),
            "features": features,
            "feature_version": FEATURE_VERSION,
            "valid_from": format_ts(t),
            "valid_until": format_ts(max(valid_until, t)),
        }
        schema.validate(snap, "snapshot")
        return snap

    def _decide(self, t: int, action: str, reasons: list[str], bar: Bar | None = None,
                order: dict[str, Any] | None = None, extra: dict[str, Any] | None = None,
                hypothesis: str | None = None, counter: str | None = None,
                invalidate: str | None = None) -> dict[str, Any]:
        self.decision_counter += 1
        valid_until = t + (self.p.signal_validity_ms * MS if order else self.p.bar_seconds * SECOND)
        snap = self._snapshot(t, bar, extra or {}, valid_until)
        st = bar.stats if bar is not None else None
        facts: dict[str, Any] = {}
        if st is not None:
            facts = {
                "buy_volume": st.buy, "sell_volume": st.sell, "unknown_volume": st.unknown,
                "delta": st.delta,
                "known_fraction": None if st.known_fraction is None else float(st.known_fraction),
                "total_volume": st.total,
                "relative_delta": None if st.relative_delta is None else float(st.relative_delta),
            }
        decision = {
            "decision_id": f"{self.ctx.session_id}-D{self.decision_counter:04d}",
            "snapshot_id": snap["snapshot_id"],
            "policy_version": self.p.policy_version,
            "mode": self.mode,
            "decided_at": format_ts(t),
            "state": self.state.value,
            "halt_new_entries": bool(self.halt_reasons),
            "action": action,
            "reason_codes": list(dict.fromkeys(reasons)),
            "facts": facts,
            "evidence_refs": [snap["snapshot_id"]] + ([f"bar-{bar.index}"] if bar is not None else []),
            "hypothesis": hypothesis,
            "counterevidence": counter,
            "would_invalidate": invalidate,
            "proposed_order": order,
            "forecast": None,
        }
        schema.validate(decision, "decision")
        if action == "PROPOSE_LONG" and not self.p.allow_long:
            raise AssertionError("PROPOSE_LONG ist in OF-A-1.0 unzulässig")
        # Snapshot und Entscheidung werden gespeichert, bevor der Replay weitere Ereignisse freigibt.
        self.journal.append("SNAPSHOT", t, snap)
        self.journal.append("DECISION", t, decision)
        self.decisions.append(decision)
        self._step(f"decision:{action}")
        return decision

    # ------------------------------------------------------------ Balken
    def on_bar(self, bar: Bar, t: int) -> None:
        self.bars.append(bar)
        self.contiguous_complete = self.contiguous_complete + 1 if bar.complete else 0
        if R.DATA_GAP in self.halt_reasons and self.contiguous_complete >= self.p.reference_bars:
            self.halt_reasons.discard(R.DATA_GAP)  # Warm-up nach Wiederherstellung abgeschlossen

        if self.state == State.DONE_FOR_SESSION:
            self._decide(t, "NO_TRADE", [R.DONE_FOR_SESSION], bar)
        elif self.state in (State.OPEN, State.EXIT_PENDING):
            self._decide(t, "NO_TRADE", [R.POSITION_MANAGED], bar)
        elif self.state == State.ENTRY_PENDING:
            self._decide(t, "NO_TRADE", [R.ENTRY_PENDING], bar)
        elif self.state == State.COOLDOWN:
            if bar.start >= (self.cooldown_since or 0):
                self.cooldown_remaining -= 1
            if self.cooldown_remaining <= 0:
                self._set_state(State.IDLE, t, "cooldown_done")
            self._decide(t, "NO_TRADE", [R.COOLDOWN], bar)
        elif self.state == State.ARMED:
            self._on_bar_armed(bar, t)
        else:
            self._on_bar_idle(bar, t)

    def breakout_reasons(self, bar: Bar, t: int) -> list[str]:
        """Prüft Kap. 7.2 für einen versiegelten Balken. Leere Liste -> Kandidat."""
        p = self.p
        P = self.ctx.prior_high_ticks
        if not self.ctx.evaluable:
            return [R.SESSION_NOT_EVALUABLE]
        if P is None:
            return [R.MISSING_HISTORY]
        if self.entries_filled >= p.max_filled_entries:
            return [R.DONE_FOR_SESSION]
        if self.candidates_count >= p.max_candidates:
            return [R.MAX_CANDIDATES]
        if self.halt_reasons:
            return sorted(self.halt_reasons)
        if not (bar.start >= self.signal_start and bar.end <= self.signal_end):
            return [R.OUTSIDE_SIGNAL_TIME]
        if self._news_hits(bar.start, bar.end, t):
            return [R.NEWS_BLACKOUT]
        if not bar.complete:
            return [R.NO_TRADE_DATA_QUALITY]
        if self.contiguous_complete - 1 < p.reference_bars:
            return [R.WARMUP]
        if bar.trade_count == 0:
            return [R.NO_SIGNAL]
        prev_close = self._previous_close()
        if prev_close is None or prev_close > P:
            return [R.NO_SIGNAL]
        if not (P + p.breakout_min_ticks <= bar.high <= P + p.breakout_max_ticks) or bar.close < P:
            return [R.NO_SIGNAL]
        out: list[str] = []
        st = bar.stats
        ref_median = median(self._reference_volumes())
        if st.total < p.min_volume or st.total < p.min_volume_multiple * ref_median:
            out.append(R.NO_SIGNAL)
        if st.known_fraction is None or st.known_fraction < p.min_known_fraction:
            out.append(R.NO_TRADE_DATA_QUALITY)
        if st.relative_delta is None or st.relative_delta < p.min_relative_delta:
            out.append(R.NO_SIGNAL)
        return list(dict.fromkeys(out))

    def _on_bar_idle(self, bar: Bar, t: int) -> None:
        reasons = self.breakout_reasons(bar, t)
        if reasons:
            if self.candidates_count >= self.p.max_candidates and self.state != State.DONE_FOR_SESSION:
                self._set_state(State.DONE_FOR_SESSION, t, "max_candidates")
            self._decide(t, "NO_TRADE", reasons, bar)
            return
        p = self.p
        P = self.ctx.prior_high_ticks
        self.candidates_count += 1
        self.candidate = Candidate(
            number=self.candidates_count, prior_high=P, high=bar.high, low=bar.low, breakout_end=bar.end,
            armed_at=t, confirm_level=min(P - p.confirmation_offset_ticks, bar.low - p.confirmation_offset_ticks),
            barrier=bar.high + p.invalidation_ticks, stop=bar.high + p.stop_ticks_above_high)
        self._set_state(State.ARMED, t, "breakout_bar")
        c = self.candidate
        self._decide(
            t, "NO_TRADE", [R.WAIT_CONFIRMATION], bar,
            extra={"candidate": asdict(c), "reference_median_volume": float(median(self._reference_volumes()))},
            hypothesis="Aufnahme aggressiver Käufe am Vortageshoch möglich",
            counter="Preisrückkehr noch nicht bestätigt",
            invalidate=f"Trade bei oder über {c.barrier} Ticks vor Bestätigung")

    def _on_bar_armed(self, bar: Bar, t: int) -> None:
        c = self.candidate
        c.bars_seen += 1
        if not bar.complete:
            self._decide(t, "NO_TRADE", [R.CANDIDATE_REJECTED_DATA, R.NO_TRADE_DATA_QUALITY], bar)
            self._reject(t, bar.end)
        elif self._news_hits(bar.start, bar.end, t):
            self._decide(t, "NO_TRADE", [R.CANDIDATE_REJECTED_DATA, R.NEWS_BLACKOUT], bar)
            self._reject(t, bar.end)
        elif bar.close is not None and bar.close <= c.confirm_level:
            c.confirmed_at = t
            self._confirm(bar, t)
        elif c.bars_seen >= self.p.confirmation_bars:
            self._decide(t, "NO_TRADE", [R.CANDIDATE_REJECTED_EXPIRY], bar)
            self._reject(t, bar.end)
        else:
            self._decide(t, "NO_TRADE", [R.WAIT_CONFIRMATION], bar, extra={"candidate": asdict(c)})

    # --------------------------------------------------- Bestätigung/Entry
    def _confirm(self, bar: Bar, t: int) -> None:
        if self.entry_filter is not None:
            self._set_state(State.ENTRY_PENDING, t, "confirmed_wait_filter")
            self._decide(t, "NO_TRADE", [R.CONFIRMED, R.WAIT_FILTER], bar)
            response_at = t + self.entry_filter.latency_ms * MS
            number = self.candidate.number
            self.scheduler.at(response_at, PRIO_TIMER, lambda now: self._on_filter_response(now, number, bar))
            return
        self._propose(bar, t, signal_time=t)

    def _on_filter_response(self, t: int, number: int, bar: Bar) -> None:
        c = self.candidate
        if c is None or c.number != number or self.state != State.ENTRY_PENDING:
            return  # Kandidat inzwischen verworfen
        if t > c.confirmed_at + self.p.signal_validity_ms * MS:
            self._decide(t, "NO_TRADE", [R.NO_TRADE_EXPIRED], bar,
                         extra={"filter_latency_ms": self.entry_filter.latency_ms})
            self._reject(t)
        elif not self.entry_filter.approve:
            self._decide(t, "NO_TRADE", [R.FILTER_REJECTED], bar)
            self._reject(t)
        else:
            self._propose(bar, t, signal_time=c.confirmed_at)

    def _propose(self, bar: Bar, t: int, signal_time: int) -> None:
        p, c = self.p, self.candidate
        valid_until = signal_time + p.signal_validity_ms * MS
        if t > valid_until:
            self._decide(t, "NO_TRADE", [R.NO_TRADE_EXPIRED], bar)
            return self._reject(t, bar.end)
        if t >= self.entry_cutoff:
            self._decide(t, "NO_TRADE", [R.AFTER_ENTRY_CUTOFF], bar)
            return self._reject(t, bar.end)
        q = self.market.quote
        planned = (q.bid_ticks if q is not None else bar.close) - self.sim.extra_slippage_ticks
        distance, target = short_levels(planned, c.stop, p.target_multiple)
        if not (p.min_distance_ticks <= distance <= p.max_distance_ticks):
            self._decide(t, "NO_TRADE", [R.CONFIRMED, R.RISK_DISTANCE_OUT_OF_RANGE], bar,
                         extra={"planned_fill_ticks": planned, "distance_ticks": distance})
            return self._reject(t, bar.end)
        size = position_size(p.budget_usd, distance, self.contract.tick_value_usd, self.sim.roundtrip_fee_usd,
                             self.sim.sizing_slippage_reserve_ticks, p.max_contracts)
        if size.quantity == 0:
            self._decide(t, "NO_TRADE", [R.CONFIRMED, R.NO_TRADE_RISK], bar,
                         extra={"risk_per_contract_usd": str(size.risk_per_contract)})
            return self._reject(t, bar.end)
        cid = self._cid(c.number, "ENTRY")
        c.entry_client_id = cid
        self.entry_meta[cid] = {"candidate_number": c.number, "stop_ticks": c.stop}
        order = {
            "client_order_id": cid,
            "side": "SELL",
            "quantity": size.quantity,
            "entry_rule": "MARKET_SELL_AT_BID_AFTER_LATENCY",
            "planned_fill_ticks": planned,
            "stop_ticks": c.stop,
            "target_ticks": target,
            "distance_ticks": distance,
            "valid_for_ms": max(1, (valid_until - t) // MS),
            "valid_until": format_ts(valid_until),
            "planned_risk_usd": str(size.risk_per_contract * size.quantity),
        }
        self._set_state(State.ENTRY_PENDING, t, "confirmed")
        decision = self._decide(
            t, "PROPOSE_SHORT", [R.CONFIRMED], bar, order=order,
            extra={"candidate": asdict(c)},
            hypothesis="Fehlgeschlagener Ausbruch über das Vortageshoch",
            counter="Erneuter Anstieg über das Ausbruchshoch möglich",
            invalidate=f"Trade bei oder über {c.barrier} Ticks vor dem Entry-Fill")
        intent = EntryIntent(
            client_order_id=cid, decision_id=decision["decision_id"], candidate_number=c.number,
            side="SELL", quantity=size.quantity,
            approved_quantity=min(size.quantity, p.max_contracts), stop_ticks=c.stop,
            target_multiple=p.target_multiple, signal_time=signal_time, valid_until=valid_until,
            entry_cutoff=self.entry_cutoff)
        self.execution.submit_entry(intent, t)
        self._step("intent:entry")

    def _reject(self, t: int, ref_exchange_ts: int | None = None) -> None:
        """Verwirft den Kandidaten. Die Wartezeit zählt nur Balken, die bei oder nach
        ref_exchange_ts (Börsenzeit des verwerfenden Ereignisses) beginnen."""
        self.candidate = None
        if self.candidates_count >= self.p.max_candidates or self.entries_filled >= self.p.max_filled_entries:
            self._set_state(State.DONE_FOR_SESSION, t, "candidate_rejected_final")
            return
        self.cooldown_remaining = self.p.cooldown_bars
        self.cooldown_since = t if ref_exchange_ts is None else ref_exchange_ts
        self._set_state(State.COOLDOWN, t, "candidate_rejected")

    # ------------------------------------------------------------- Trades
    def on_trade(self, e: MarketEvent, t: int) -> None:
        c = self.candidate
        if (c is not None and self.state in (State.ARMED, State.ENTRY_PENDING)
                and e.exchange_ts >= c.breakout_end and e.price_ticks >= c.barrier):
            # Verwerfung hat Vorrang vor einem späteren Schlusskurs (Kap. 7.3, T11).
            if self.state == State.ENTRY_PENDING and c.entry_client_id is not None:
                self._decide(t, "CANCEL_PENDING", [R.CANDIDATE_REJECTED_BARRIER],
                             extra={"barrier_trade": e.event_id, "price_ticks": e.price_ticks})
                self.execution.cancel_entry(c.entry_client_id, t)
            else:
                self._decide(t, "NO_TRADE", [R.CANDIDATE_REJECTED_BARRIER],
                             extra={"barrier_trade": e.event_id, "price_ticks": e.price_ticks})
            self._reject(t, e.exchange_ts)
            return
        pos = self.position
        if pos is not None and self.state == State.OPEN and e.exchange_ts >= pos.entry_ts:
            if e.price_ticks >= pos.stop_ticks:
                self._exit("STOP", t, e.event_id)
            elif e.price_ticks <= pos.target_ticks:
                self._exit("TARGET", t, e.event_id)

    # ------------------------------------------------------ Ausführung
    def on_entry_filled(self, fill: Fill, t: int) -> None:
        p = self.p
        meta = self.entry_meta.get(fill.client_order_id)
        if meta is None:
            raise RuntimeError(f"Fill ohne bekannte Entry-Order {fill.client_order_id}")
        number, stop = meta["candidate_number"], meta["stop_ticks"]
        distance, target = short_levels(fill.price_ticks, stop, p.target_multiple)
        self.entries_filled += 1
        self.position = Position(
            candidate_number=number, quantity=fill.quantity, entry_ticks=fill.price_ticks, entry_ts=fill.ts,
            stop_ticks=stop, distance_ticks=distance, target_ticks=target,
            time_exit_at=min(fill.ts + p.time_exit_minutes * MINUTE, self.entry_cutoff),
            entry_client_id=fill.client_order_id, exit_client_id=self._cid(number, "EXIT"))
        self.candidate = None
        self._set_state(State.OPEN, t, "entry_filled" + ("_after_cancel" if fill.after_cancel_request else ""))
        self._arm_time_exit()
        rpc_ok = position_size(p.budget_usd, max(distance, 1), self.contract.tick_value_usd,
                               self.sim.roundtrip_fee_usd, self.sim.sizing_slippage_reserve_ticks,
                               p.max_contracts).quantity >= fill.quantity
        if not (p.min_distance_ticks <= distance <= p.max_distance_ticks) or not rpc_ok:
            # Sofortausstiegsregel bei unerwartet schlechter Ausführung (Kap. 8.1).
            self.position.risk_overrun = True
            self._exit("RISK_OVERRUN", t, fill.client_order_id)

    def _arm_time_exit(self) -> None:
        pos = self.position
        at = max(pos.time_exit_at, self.scheduler.now)
        cid = pos.exit_client_id
        self.scheduler.at(at, PRIO_TIMER, lambda now: self._on_time_exit(now, cid))

    def _on_time_exit(self, t: int, exit_cid: str) -> None:
        if self.position is not None and self.position.exit_client_id == exit_cid and self.state == State.OPEN:
            self._exit("TIME", t, "time_exit")

    def on_entry_failed(self, status: str, reasons: list[str], t: int) -> None:
        if self.state != State.ENTRY_PENDING:
            return  # bereits verworfen (z. B. Cancel nach Barrieretreffer)
        code = R.AFTER_ENTRY_CUTOFF if "AFTER_ENTRY_CUTOFF" in reasons else R.NO_TRADE_EXPIRED
        self._decide(t, "NO_TRADE", [code], extra={"execution_status": status, "execution_reasons": reasons})
        self._reject(t)

    def _exit(self, reason: str, t: int, ref: str) -> None:
        pos = self.position
        if pos is None or self.state != State.OPEN:
            return  # erste Exit-Ursache sperrt weitere Exit-Aufträge
        pos.exit_reason = reason
        self._set_state(State.EXIT_PENDING, t, f"exit_{reason.lower()}")
        self.journal.append("EXIT_TRIGGER", t, {"reason": reason, "ref": ref, "client_order_id": pos.exit_client_id})
        self.execution.submit_exit(ExitIntent(pos.exit_client_id, "BUY", pos.quantity, reason, t, ref), t)
        self._step("intent:exit")

    def on_exit_filled(self, fill: Fill, t: int) -> None:
        pos = self.position
        pnl = short_trade_pnl(pos.entry_ticks, fill.price_ticks, pos.quantity, self.contract,
                              self.sim.roundtrip_fee_usd, pos.distance_ticks)
        result = {
            "session_id": self.ctx.session_id,
            "candidate_number": pos.candidate_number,
            "entry_client_id": pos.entry_client_id,
            "exit_client_id": pos.exit_client_id,
            "entry_ticks": pos.entry_ticks,
            "exit_ticks": fill.price_ticks,
            "entry_ts": format_ts(pos.entry_ts),
            "exit_ts": format_ts(fill.ts),
            "quantity": pos.quantity,
            "stop_ticks": pos.stop_ticks,
            "target_ticks": pos.target_ticks,
            "distance_ticks": pos.distance_ticks,
            "exit_reason": pos.exit_reason,
            "pnl_ticks": pnl.ticks,
            "gross_usd": str(pnl.gross_usd),
            "fees_usd": str(pnl.fees_usd),
            "net_usd": str(pnl.net_usd),
            "r_usd": str(pnl.r_usd) if pnl.r_usd is not None else None,
            "net_r": str(pnl.net_r) if pnl.net_r is not None else None,
            "risk_overrun": pos.risk_overrun,
            "scorable": not pos.unscorable,
            "unscorable_reasons": list(pos.unscorable),
            "outcome": "UNSCORABLE" if pos.unscorable else "SCORED",
        }
        self.results.append(result)
        self.journal.append("RESULT", t, result)
        self.position = None
        if self.entries_filled >= self.p.max_filled_entries:
            self._set_state(State.DONE_FOR_SESSION, t, "flat_after_exit")
        else:
            self.cooldown_remaining = self.p.cooldown_bars
            self.cooldown_since = t
            self._set_state(State.COOLDOWN, t, "flat_after_exit")

    # ------------------------------------------------------ Datenqualität
    def on_data_problem(self, kind: str, t: int, detail: str) -> None:
        """kind: DATA_GAP oder TRADING_HALT. Sperrt neue Einstiege, Schutz offener Positionen bleibt."""
        if kind == R.DATA_GAP:
            self.contiguous_complete = 0
        already = kind in self.halt_reasons
        self.halt_reasons.add(kind)
        if self.state in (State.ARMED, State.ENTRY_PENDING) and self.candidate is not None:
            c = self.candidate
            if self.state == State.ENTRY_PENDING and c.entry_client_id is not None:
                self._decide(t, "CANCEL_PENDING", [R.CANDIDATE_REJECTED_DATA, kind], extra={"detail": detail})
                self.execution.cancel_entry(c.entry_client_id, t)
            else:
                self._decide(t, "NO_TRADE", [R.CANDIDATE_REJECTED_DATA, kind], extra={"detail": detail})
            self._reject(t)
        if self.position is not None and kind == R.DATA_GAP:
            # Ergebnis nicht rekonstruierbar: kein Auswählen des günstigeren Ausstiegs (T18).
            if R.DATA_GAP not in self.position.unscorable:
                self.position.unscorable.append(R.DATA_GAP)
                self.journal.append("UNSCORABLE", t, {"reason": R.DATA_GAP, "detail": detail})
        if not already:
            self._decide(t, "HALT_NEW_ENTRIES", [kind], extra={"detail": detail})

    def on_market_open(self, t: int) -> None:
        self.halt_reasons.discard(R.TRADING_HALT)
        self.contiguous_complete = 0  # Warm-up nach Wiederaufnahme

    def finish(self, t: int) -> None:
        if self.state in (State.ARMED, State.ENTRY_PENDING) and self.candidate is not None:
            if self.candidate.entry_client_id is not None:
                self.execution.cancel_entry(self.candidate.entry_client_id, t)
            self._decide(t, "NO_TRADE", [R.CANDIDATE_REJECTED_DATA, R.AFTER_ENTRY_CUTOFF])
            self._reject(t)
        if self.position is not None:
            if "SESSION_END_OPEN" not in self.position.unscorable:
                self.position.unscorable.append("SESSION_END_OPEN")
            self.journal.append("UNSCORABLE", t, {"reason": "SESSION_END_OPEN"})

    # ------------------------------------------------------------ Neustart
    def restore(self, t: int, entry_fills: list[Fill], exit_fills: list[Fill],
                working_entry: list[str], working_exit: list[str], entry_meta: dict[str, dict]) -> None:
        """Rekonstruiert den Zustand nach einem Neustart (Kap. 5.4, T17).

        Maßgeblich für die Position sind die bestätigten Fills aus dem Journal, nicht der
        letzte Checkpoint. Ausstehende, nicht gefüllte Entry-Orders werden storniert."""
        checkpoint = None
        for rec in self.journal.records("STATE"):
            checkpoint = rec["payload"]
        self.decision_counter = sum(1 for _ in self.journal.records("DECISION"))
        if checkpoint is not None:
            self.candidates_count = checkpoint["candidates_count"]
            self.halt_reasons = set(checkpoint["halt_reasons"])
        self.entries_filled = len(entry_fills)
        self.candidates_count = max(self.candidates_count, self.entries_filled)
        self.contiguous_complete = 0
        open_qty = sum(f.quantity for f in entry_fills) - sum(f.quantity for f in exit_fills)
        self.entry_meta.update(entry_meta)
        if open_qty > 0:
            ef = entry_fills[-1]
            meta = entry_meta[ef.client_order_id]
            number = meta["candidate_number"]
            distance, target = short_levels(ef.price_ticks, meta["stop_ticks"], self.p.target_multiple)
            prev = (checkpoint or {}).get("position") or {}
            self.position = Position(
                candidate_number=number, quantity=open_qty, entry_ticks=ef.price_ticks, entry_ts=ef.ts,
                stop_ticks=meta["stop_ticks"], distance_ticks=distance, target_ticks=target,
                time_exit_at=min(ef.ts + self.p.time_exit_minutes * MINUTE, self.entry_cutoff),
                entry_client_id=ef.client_order_id, exit_client_id=self._cid(number, "EXIT"),
                unscorable=list(prev.get("unscorable", [])), exit_reason=prev.get("exit_reason"),
                risk_overrun=prev.get("risk_overrun", False))
            self.state = State.EXIT_PENDING if working_exit else State.OPEN
            self.journal.append("STATE", t, self.core_state())
            self._decide(t, "NO_TRADE", [R.RESTART_RECOVERY, R.POSITION_MANAGED])
            if self.state == State.OPEN:
                self._arm_time_exit()
            return
        self.entry_meta.update(entry_meta)
        self.position = None
        self.state = State.DONE_FOR_SESSION if (
            self.entries_filled >= self.p.max_filled_entries or self.candidates_count >= self.p.max_candidates
        ) else State.COOLDOWN
        self.cooldown_remaining = self.p.cooldown_bars
        self.cooldown_since = t
        self.journal.append("STATE", t, self.core_state())
        self._decide(t, "NO_TRADE", [R.RESTART_RECOVERY])
