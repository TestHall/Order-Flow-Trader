"""Kernberechnungen Kap. 6.1. Division durch null ergibt None plus Qualitätsflag, nie 0 oder neutral."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable

FLAG_ZERO_VOLUME = "ZERO_VOLUME"              # V = 0 -> K = None
FLAG_NO_CLASSIFIED_VOLUME = "NO_CLASSIFIED_VOLUME"  # B + S = 0 -> DR = None
FLAG_ZERO_DENOMINATOR = "ZERO_DENOMINATOR"    # Kap. 6.2 Imbalance


@dataclass(frozen=True)
class WindowStats:
    buy: int
    sell: int
    unknown: int
    total: int                     # V = B + S + U
    delta: int                     # D = B - S (nur bekannter Anteil)
    known_fraction: Fraction | None  # K = (B + S) / V
    relative_delta: Fraction | None  # DR = D / (B + S)
    flags: tuple[str, ...]


def window_stats(buy: int, sell: int, unknown: int) -> WindowStats:
    for name, v in (("B", buy), ("S", sell), ("U", unknown)):
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"{name} muss eine nichtnegative ganze Zahl sein: {v!r}")
    total = buy + sell + unknown
    classified = buy + sell
    flags = []
    if total > 0:
        known = Fraction(classified, total)
    else:
        known = None
        flags.append(FLAG_ZERO_VOLUME)
    if classified > 0:
        rel = Fraction(buy - sell, classified)
    else:
        rel = None
        flags.append(FLAG_NO_CLASSIFIED_VOLUME)
    return WindowStats(buy, sell, unknown, total, buy - sell, known, rel, tuple(flags))


def vwap(trades: Iterable[tuple[int, int]]) -> Fraction | None:
    """sum(price x size) / sum(size) in Ticks; None bei Volumen 0."""
    num = den = 0
    for price, size in trades:
        num += price * size
        den += size
    return Fraction(num, den) if den else None


def spread_ticks(bid_ticks: int | None, ask_ticks: int | None) -> int | None:
    if bid_ticks is None or ask_ticks is None:
        return None
    return ask_ticks - bid_ticks


def median(values: list[int]) -> Fraction:
    if not values:
        raise ValueError("Median einer leeren Menge")
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return Fraction(s[mid]) if n % 2 else Fraction(s[mid - 1] + s[mid], 2)


@dataclass(frozen=True)
class Imbalance:
    status: str            # "OK" | "ZERO_DENOMINATOR" | "BELOW_MIN"
    ratio: Fraction | None


def diagonal_buy_imbalance(ask_vol_at_p: int, bid_vol_at_p_minus_1: int, min_volume: int) -> Imbalance:
    """AskVol(p) gegen BidVol(p - 1 Tick), Kap. 6.2. In OF-A-1.0 nur protokolliert, kein Filter."""
    if bid_vol_at_p_minus_1 == 0:
        return Imbalance(FLAG_ZERO_DENOMINATOR, None)
    if ask_vol_at_p + bid_vol_at_p_minus_1 < min_volume:
        return Imbalance("BELOW_MIN", Fraction(ask_vol_at_p, bid_vol_at_p_minus_1))
    return Imbalance("OK", Fraction(ask_vol_at_p, bid_vol_at_p_minus_1))
