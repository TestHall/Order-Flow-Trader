"""Ergebnisrechnung (Kap. 8.2, 12.3, T05, T06).

Kostenansatz 1: tatsächliche Fill-Preise enthalten Spread und Slippage; nur Gebühren werden
explizit abgezogen. Die Reserve aus der Positionsgrößenberechnung ist kein zweiter Kostenabzug.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from orderflow.common.units import ContractSpec


@dataclass(frozen=True)
class TradePnL:
    ticks: int
    gross_usd: Decimal
    fees_usd: Decimal
    net_usd: Decimal
    r_usd: Decimal | None       # anfängliches tatsächliches Preisrisiko nach Fill, vor Gebühren
    net_r: Decimal | None


def short_trade_pnl(entry_ticks: int, exit_ticks: int, quantity: int, contract: ContractSpec,
                    roundtrip_fee: Decimal, initial_distance_ticks: int | None = None) -> TradePnL:
    ticks = entry_ticks - exit_ticks
    gross = ticks * quantity * contract.tick_value_usd
    fees = roundtrip_fee * quantity
    net = gross - fees
    r_usd = net_r = None
    if initial_distance_ticks is not None and initial_distance_ticks > 0:
        r_usd = initial_distance_ticks * quantity * contract.tick_value_usd
        net_r = net / r_usd
    return TradePnL(ticks, gross, fees, net, r_usd, net_r)


def expectancy_r(win_rate: Fraction, win_r: Fraction, loss_rate: Fraction, loss_r: Fraction,
                 cost_r: Fraction) -> Fraction:
    """Erwartungswert je Trade in R nach Kosten (T06): p_w x W - p_l x L - Kosten."""
    if win_rate + loss_rate != 1:
        raise ValueError("Gewinn- und Verlustanteil müssen 1 ergeben")
    return win_rate * win_r - loss_rate * loss_r - cost_r
