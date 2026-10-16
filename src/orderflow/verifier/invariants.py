"""Invarianten des Zustandsautomaten (Kap. 7.5), geprüft bei jeder Zustandsänderung.

Nie Long; nie mehr als ein Kontrakt; nie eine Order ohne eindeutige ID; kein zweiter Entry bei
offenem oder ausstehendem Entry; kein neuer Trade nach dem ersten Fill der Sitzung; kein erneuter
Exit nach vollständigem Glattstellen; kein Verlust der Position durch Neustart oder Datenstörung.
"""
from __future__ import annotations

from typing import Any

TERMINAL = {"FILLED", "EXPIRED", "CANCELLED", "REJECTED"}


class InvariantViolation(AssertionError):
    pass


class InvariantMonitor:
    def __init__(self, max_contracts: int = 1, max_filled_entries: int = 1):
        self.max_contracts = max_contracts
        self.max_filled_entries = max_filled_entries
        self.checks = 0
        self.labels: list[str] = []

    def __call__(self, runner: Any, label: str) -> None:
        self.check(runner.state_view(), label)

    def check(self, view: dict[str, Any], label: str = "") -> None:
        self.checks += 1
        self.labels.append(label)
        eng, orders, fills = view["engine"], view["orders"], view["fills"]
        pos = view["position_from_fills"]

        def fail(msg: str) -> None:
            raise InvariantViolation(f"[{label}] {msg}")

        if pos > 0:
            fail("Long-Position")
        if any(o["kind"] == "ENTRY" and o["side"] != "SELL" for o in orders):
            fail("Long-Entry-Order")
        if abs(pos) > self.max_contracts or any(o["quantity"] > self.max_contracts for o in orders):
            fail("mehr als ein Kontrakt")
        ids = [o["client_order_id"] for o in orders]
        if any(not i for i in ids) or len(ids) != len(set(ids)):
            fail("Order ohne eindeutige ID")
        working_entries = [o for o in orders if o["kind"] == "ENTRY" and o["status"] not in TERMINAL]
        if len(working_entries) > 1 or (working_entries and pos != 0):
            fail("zweiter Entry bei offenem oder ausstehendem Entry")
        entry_fills = sorted(f["ts"] for f in fills if any(
            o["client_order_id"] == f["client_order_id"] and o["kind"] == "ENTRY" for o in orders))
        if len(entry_fills) > self.max_filled_entries:
            fail("mehr gefüllte Einstiege als erlaubt")
        if entry_fills and any(o["kind"] == "ENTRY" and o["created_at"] > entry_fills[0] for o in orders):
            fail("neuer Entry nach dem ersten Fill der Sitzung")
        exit_qty = sum(f["quantity"] for f in fills if f["side"] == "BUY")
        entry_qty = sum(f["quantity"] for f in fills if f["side"] == "SELL")
        if exit_qty > entry_qty:
            fail("Exit nach vollständigem Glattstellen")
        if pos == 0 and any(o["kind"] == "EXIT" and o["status"] not in TERMINAL for o in orders):
            fail("offene Exit-Order ohne Position")
        engine_pos = eng["position"]["quantity"] if eng["position"] else 0
        if engine_pos != -pos:
            fail(f"Positionsverlust: Regelmodul {engine_pos}, Fills {pos}")
        if eng["state"] in ("OPEN", "EXIT_PENDING") and pos == 0:
            fail(f"Zustand {eng['state']} ohne Position")
        if eng["state"] in ("IDLE", "ARMED") and pos != 0:
            fail(f"Zustand {eng['state']} mit offener Position")
