"""Fill-Simulator ohne fiktive Fills (Kap. 7.4, 8.2).

Modell (Details in docs/ANNAHMEN.md):
- Eine Entry-Order wird zum Entscheidungszeitpunkt gesendet und trifft nach der Orderverzögerung
  ein. Erst dann prüft das Ausführungsmodul am dann verfügbaren Markt Signalalter, Zeit,
  Quote-Alter, Spread, sichtbare Menge, Stoppabstand und Geldrisiko.
- Verkauf nur zum sichtbaren Bid, Rückkauf nur zum sichtbaren Ask, jeweils abzüglich bzw.
  zuzüglich modellierter Zusatz-Slippage. Ohne sichtbare Menge kein Fill.
- Scheitert die Prüfung, wartet die Order auf den nächsten Quote und wird dann erneut gesendet,
  aber nur innerhalb der restlichen Signalgültigkeit (T13).
- Ein Cancel braucht ebenfalls die Orderverzögerung. Eine bereits gesendete Order kann deshalb
  trotz Cancel gefüllt werden; der Fill wird dann als offene Position verwaltet (Kap. 7.4).
"""
from __future__ import annotations

from typing import Protocol

from orderflow.common.config import Policy, SimConfig
from orderflow.common.contracts import EntryIntent, ExitIntent, Fill
from orderflow.common.scheduler import PRIO_TIMER, Scheduler
from orderflow.common.timeutil import MS
from orderflow.common.units import ContractSpec
from orderflow.execution.orders import OrderManager
from orderflow.execution.risk import position_size, short_levels
from orderflow.features.market import MarketView


class ExecutionListener(Protocol):
    def on_entry_filled(self, fill: Fill, t: int) -> None: ...
    def on_entry_failed(self, status: str, reasons: list[str], t: int) -> None: ...
    def on_exit_filled(self, fill: Fill, t: int) -> None: ...


class ExecutionSimulator:
    def __init__(self, scheduler: Scheduler, market: MarketView, orders: OrderManager, policy: Policy,
                 sim: SimConfig, contract: ContractSpec):
        self.scheduler = scheduler
        self.market = market
        self.orders = orders
        self.p = policy
        self.sim = sim
        self.contract = contract
        self.listener: ExecutionListener | None = None
        self.on_step = None
        self._entry: EntryIntent | None = None
        self._in_flight = False
        self._cancel_requested = False
        self._last_reasons: list[str] = []
        self._exit: ExitIntent | None = None
        self._exit_live = False
        self._exit_arrival = 0

    def _step(self, label: str) -> None:
        if self.on_step is not None:
            self.on_step(label)

    # ------------------------------------------------------------ Entry
    def submit_entry(self, intent: EntryIntent, t: int) -> None:
        if intent.side != "SELL":
            raise ValueError("OF-A-1.0 erlaubt nur Short-Einstiege")
        order, created = self.orders.submit(
            intent.client_order_id, "ENTRY", intent.side, intent.quantity, intent.approved_quantity, t,
            meta={"stop_ticks": intent.stop_ticks, "candidate_number": intent.candidate_number,
                  "decision_id": intent.decision_id, "valid_until": intent.valid_until})
        if not created:
            return  # Idempotenz: gleiche Client-ID erzeugt keine zweite Order (T16)
        if order.status == "REJECTED":
            self.listener.on_entry_failed("REJECTED", order.reasons, t)
            return
        others = [o for o in self.orders.working("ENTRY") if o.client_order_id != order.client_order_id]
        if self.orders.position() != 0 or others:
            self.orders.set_status(order.client_order_id, "REJECTED", t, ["POSITION_OR_ORDER_OPEN"])
            self.listener.on_entry_failed("REJECTED", ["POSITION_OR_ORDER_OPEN"], t)
            return
        self.orders.set_status(order.client_order_id, "WORKING", t)
        self._entry = intent
        self._cancel_requested = False
        self._last_reasons = []
        self._send(t)
        deadline = min(intent.valid_until, intent.entry_cutoff)
        self.scheduler.at(max(deadline, t), PRIO_TIMER, lambda now: self._deadline(now, intent.client_order_id))

    def _send(self, t: int) -> None:
        self._in_flight = True
        cid = self._entry.client_order_id
        self.scheduler.at(t + self.sim.order_latency_ms * MS, PRIO_TIMER, lambda now: self._arrive(now, cid))

    def _deadline(self, t: int, cid: str) -> None:
        e = self._entry
        if e is None or e.client_order_id != cid or self._in_flight:
            return
        self._expire(t)

    def _expire(self, t: int) -> None:
        e = self._entry
        reasons = ["AFTER_ENTRY_CUTOFF" if t >= e.entry_cutoff else "SIGNAL_EXPIRED"] + self._last_reasons
        self._entry = None
        self.orders.set_status(e.client_order_id, "EXPIRED", t, reasons)
        self.listener.on_entry_failed("EXPIRED", reasons, t)

    def entry_gate(self, intent: EntryIntent, t: int) -> tuple[list[str], int | None]:
        """Prüfungen am dann verfügbaren Markt. Rückgabe (Gründe, Fill-Preis)."""
        q = self.market.quote
        if q is None:
            return ["QUOTE_MISSING"], None
        reasons = []
        if t - q.receive_ts > self.p.max_quote_age_ms * MS:
            reasons.append("QUOTE_STALE")
        if q.bid_ticks >= q.ask_ticks:
            reasons.append("CROSSED_QUOTE")
        if q.spread_ticks > self.p.max_spread_ticks:
            reasons.append("SPREAD_TOO_WIDE")
        if q.bid_size < intent.quantity:
            reasons.append("NO_VISIBLE_LIQUIDITY")
        price = q.bid_ticks - self.sim.extra_slippage_ticks
        distance, _ = short_levels(price, intent.stop_ticks, intent.target_multiple)
        if not (self.p.min_distance_ticks <= distance <= self.p.max_distance_ticks):
            reasons.append("RISK_DISTANCE_OUT_OF_RANGE")
        elif position_size(self.p.budget_usd, distance, self.contract.tick_value_usd, self.sim.roundtrip_fee_usd,
                           self.sim.sizing_slippage_reserve_ticks, self.p.max_contracts).quantity < intent.quantity:
            reasons.append("NO_TRADE_RISK")
        if self.orders.position() != 0:
            reasons.append("POSITION_NOT_FLAT")
        return reasons, (None if reasons else price)

    def _arrive(self, t: int, cid: str) -> None:
        e = self._entry
        if e is None or e.client_order_id != cid:
            return
        self._in_flight = False
        if t > e.valid_until or t >= e.entry_cutoff:
            return self._expire(t)
        reasons, price = self.entry_gate(e, t)
        if not reasons:
            q = self.market.quote
            fill = Fill(e.client_order_id, "SELL", e.quantity, price, t, q.event_id, q.bid_ticks, q.ask_ticks,
                        t - q.receive_ts, self.sim.extra_slippage_ticks,
                        after_cancel_request=self._cancel_requested)
            self._entry = None
            self.orders.record_fill(fill)
            self.listener.on_entry_filled(fill, t)
            self._step("fill:entry")
            return
        self._last_reasons = reasons
        if self._cancel_requested:
            self._entry = None
            self.orders.set_status(cid, "CANCELLED", t, ["CANCEL_REQUESTED"] + reasons)
            return
        # sonst: Order wartet auf den nächsten Quote (innerhalb der Signalgültigkeit)

    def cancel_entry(self, client_order_id: str, t: int) -> None:
        e = self._entry
        if e is None or e.client_order_id != client_order_id:
            return
        if self._in_flight:
            # Der Cancel trifft erst nach t + Verzögerung ein, also nach der bereits gesendeten Order.
            self._cancel_requested = True
            self.orders.journal.append("CANCEL_REQUEST", t, {"client_order_id": client_order_id, "in_flight": True})
            return
        self._entry = None
        self.orders.set_status(client_order_id, "CANCELLED", t, ["CANCEL_REQUESTED"])

    def on_quote(self, t: int) -> None:
        e = self._entry
        if e is not None and not self._in_flight and not self._cancel_requested \
                and t <= min(e.valid_until, e.entry_cutoff):
            self._send(t)
        if self._exit is not None and self._exit_live:
            self._try_exit(t)

    # ------------------------------------------------------------- Exit
    def submit_exit(self, intent: ExitIntent, t: int) -> None:
        if self.orders.position() >= 0:
            raise RuntimeError("Exit ohne offene Short-Position")
        order, created = self.orders.submit(intent.client_order_id, "EXIT", intent.side, intent.quantity,
                                            -self.orders.position(), t,
                                            meta={"reason": intent.reason, "trigger_ref": intent.trigger_ref})
        if not created:
            return
        self.orders.set_status(order.client_order_id, "WORKING", t)
        self._exit = intent
        self._exit_live = False
        self._exit_arrival = t + self.sim.exit_latency_ms * MS
        self.scheduler.at(self._exit_arrival, PRIO_TIMER, self._exit_arrive)

    def resume_exit(self, intent: ExitIntent, t: int) -> None:
        """Nach Neustart: bestehende, noch nicht gefüllte Exit-Order weiter bearbeiten."""
        self._exit = intent
        self._exit_live = True
        self._exit_arrival = t
        self._try_exit(t)

    def _exit_arrive(self, t: int) -> None:
        self._exit_live = True
        self._try_exit(t)

    def _try_exit(self, t: int) -> None:
        x = self._exit
        q = self.market.quote
        if q is None or q.ask_size < x.quantity or q.bid_ticks >= q.ask_ticks:
            return  # keine sichtbar ausführbare Menge: kein Fill zum besten Preis erfinden
        price = q.ask_ticks + self.sim.extra_slippage_ticks
        fill = Fill(x.client_order_id, "BUY", x.quantity, price, t, q.event_id, q.bid_ticks, q.ask_ticks,
                    t - q.receive_ts, self.sim.extra_slippage_ticks,
                    delayed_for_liquidity=t > self._exit_arrival)
        self._exit = None
        self._exit_live = False
        self.orders.record_fill(fill)
        self.listener.on_exit_filled(fill, t)
        self._step("fill:exit")
