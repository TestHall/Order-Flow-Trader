"""Preise als ganze Ticks, Geld als Decimal (Handbuch Kap. 4, 5.1)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal

from orderflow.common.paths import CONFIG_DIR


class PriceGridError(ValueError):
    """Preis liegt nicht auf dem Tickraster (Pflichtprüfung Preisraster, Kap. 4.2)."""


@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    venue: str
    tick_size: Decimal
    point_value_usd: Decimal

    @property
    def tick_value_usd(self) -> Decimal:
        return self.tick_size * self.point_value_usd

    def price_to_ticks(self, price: Decimal | str | int) -> int:
        quotient = Decimal(str(price)) / self.tick_size
        if quotient != quotient.to_integral_value():
            raise PriceGridError(f"{price} liegt nicht auf dem Raster {self.tick_size}")
        return int(quotient)

    def ticks_to_price(self, ticks: int) -> Decimal:
        return ticks * self.tick_size


def load_contract(symbol: str = "MES") -> ContractSpec:
    data = tomllib.loads((CONFIG_DIR / "contracts" / f"{symbol}.toml").read_text(encoding="utf-8"))
    return ContractSpec(
        symbol=data["symbol"],
        venue=data["venue"],
        tick_size=Decimal(data["tick_size"]),
        point_value_usd=Decimal(data["point_value_usd"]),
    )
