"""Orderverwaltung mit eindeutiger Client-ID und Idempotenz (Kap. 5.4, T16, T17)."""
from __future__ import annotations

from dataclasses import dataclass, field

from orderflow.common.contracts import Fill
from orderflow.journal.journal import Journal

TERMINAL = {"FILLED", "EXPIRED", "CANCELLED", "REJECTED"}


@dataclass
class Order:
    client_order_id: str
    kind: str                 # ENTRY | EXIT
    side: str
    quantity: int
    approved_quantity: int
    created_at: int
    status: str = "NEW"       # NEW | WORKING | FILLED | EXPIRED | CANCELLED | REJECTED
    reasons: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def record(self) -> dict:
        return {
            "client_order_id": self.client_order_id, "kind": self.kind, "side": self.side,
            "quantity": self.quantity, "approved_quantity": self.approved_quantity,
            "created_at": self.created_at, "meta": self.meta,
        }


class OrderManager:
    def __init__(self, journal: Journal):
        self.journal = journal
        self.orders: dict[str, Order] = {}
        self.fills: list[Fill] = []

    def submit(self, client_order_id: str, kind: str, side: str, quantity: int, approved_quantity: int,
               ts: int, meta: dict | None = None) -> tuple[Order, bool]:
        """Legt eine Order an. Gleiche Client-ID -> bestehende Order, keine zweite (T16).

        Rückgabe (order, created)."""
        if not client_order_id:
            raise ValueError("Order ohne eindeutige ID")
        existing = self.orders.get(client_order_id)
        if existing is not None:
            return existing, False
        order = Order(client_order_id, kind, side, quantity, approved_quantity, ts, meta=dict(meta or {}))
        if quantity < 1 or quantity > approved_quantity:
            order.status = "REJECTED"
            order.reasons.append("QUANTITY_ABOVE_APPROVED" if quantity > approved_quantity else "QUANTITY_INVALID")
        self.orders[client_order_id] = order
        self.journal.append("ORDER", ts, order.record())
        if order.status == "REJECTED":
            self.journal.append("ORDER_STATUS", ts, {"client_order_id": client_order_id, "status": "REJECTED",
                                                     "reasons": order.reasons})
        return order, True

    def set_status(self, client_order_id: str, status: str, ts: int, reasons: list[str] | None = None) -> None:
        order = self.orders[client_order_id]
        if order.status in TERMINAL:
            raise RuntimeError(f"Order {client_order_id} ist bereits {order.status}")
        order.status = status
        order.reasons = list(reasons or [])
        self.journal.append("ORDER_STATUS", ts, {"client_order_id": client_order_id, "status": status,
                                                 "reasons": order.reasons})

    def record_fill(self, fill: Fill) -> None:
        order = self.orders[fill.client_order_id]
        if order.status in TERMINAL:
            raise RuntimeError(f"Fill auf abgeschlossene Order {fill.client_order_id}")
        if fill.quantity > order.quantity:
            raise RuntimeError("Fill größer als Ordermenge")
        self.fills.append(fill)
        order.status = "FILLED"
        self.journal.append("FILL", fill.ts, fill.to_record())

    def position(self) -> int:
        return sum(f.quantity if f.side == "BUY" else -f.quantity for f in self.fills)

    def working(self, kind: str | None = None) -> list[Order]:
        return [o for o in self.orders.values()
                if o.status not in TERMINAL and (kind is None or o.kind == kind)]

    @classmethod
    def rebuild(cls, journal: Journal) -> "OrderManager":
        """Rekonstruiert bestätigten Order- und Positionsstatus aus dem Journal (Kap. 5.4, T17)."""
        om = cls(journal)
        for rec in journal.records():
            p = rec["payload"]
            if rec["kind"] == "ORDER":
                om.orders[p["client_order_id"]] = Order(
                    p["client_order_id"], p["kind"], p["side"], p["quantity"], p["approved_quantity"],
                    p["created_at"], meta=p.get("meta", {}))
            elif rec["kind"] == "ORDER_STATUS":
                o = om.orders[p["client_order_id"]]
                o.status, o.reasons = p["status"], p["reasons"]
            elif rec["kind"] == "FILL":
                om.fills.append(Fill.from_record(p))
                om.orders[p["client_order_id"]].status = "FILLED"
        return om
