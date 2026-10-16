"""Positionsgröße und Preisniveaus (Kap. 7.4, 8.1, T04, T10)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal


def risk_per_contract(distance_ticks: int, tick_value: Decimal, roundtrip_fee: Decimal,
                      reserve_ticks: int) -> Decimal:
    """Modellrisiko je Kontrakt = Stoppdistanz x Tickwert + Roundtrip-Gebühr + Slippage-Reserve."""
    if distance_ticks <= 0:
        raise ValueError(f"Stoppdistanz muss positiv sein: {distance_ticks}")
    return distance_ticks * tick_value + roundtrip_fee + reserve_ticks * tick_value


@dataclass(frozen=True)
class SizeResult:
    risk_per_contract: Decimal
    by_budget: int
    quantity: int            # nach Referenzlimit; 0 -> NO_TRADE_RISK


def position_size(budget: Decimal, distance_ticks: int, tick_value: Decimal, roundtrip_fee: Decimal,
                  reserve_ticks: int, max_contracts: int) -> SizeResult:
    rpc = risk_per_contract(distance_ticks, tick_value, roundtrip_fee, reserve_ticks)
    by_budget = int((budget / rpc).to_integral_value(rounding=ROUND_FLOOR)) if budget > 0 else 0
    return SizeResult(rpc, by_budget, max(0, min(by_budget, max_contracts)))


def short_levels(fill_ticks: int, stop_ticks: int, target_multiple: int) -> tuple[int, int]:
    """Short: d = Stopp - F, Ziel = F - multiple x d. Rückgabe (d, Ziel) in Ticks."""
    d = stop_ticks - fill_ticks
    return d, fill_ticks - target_multiple * d
