"""Prüfung einer Entscheidung unabhängig vom Regelmodul (Kap. 5.3, 10.4, 15)."""
from __future__ import annotations

from typing import Any

from orderflow.common import schema
from orderflow.common.config import Policy


def validate_decision(decision: Any, policy: Policy) -> list[str]:
    """Schemafehler und Policyverstöße. Freitextfelder werden nie als Anweisung ausgewertet."""
    problems = schema.errors(decision, "decision")
    if not isinstance(decision, dict):
        return problems
    if decision.get("action") == "PROPOSE_LONG" and not policy.allow_long:
        problems.append("PROPOSE_LONG ist in dieser Policy unzulässig")
    if decision.get("policy_version") not in (None, policy.policy_version):
        problems.append(f"falsche policy_version {decision.get('policy_version')}")
    order = decision.get("proposed_order")
    if isinstance(order, dict) and isinstance(order.get("quantity"), int) and order["quantity"] > policy.max_contracts:
        problems.append("Menge über Referenzlimit")
    return problems
