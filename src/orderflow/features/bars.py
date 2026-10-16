"""Geschlossene Balken in Börsenzeit, verfügbar erst nach Abschlussfrist (Kap. 6.1).

Intervalle sind halb offen [Beginn, Ende): ein Trade genau am Ende gehört zum nächsten Balken.
Ein Ereignis, das erst nach Versiegeln seines Balkens empfangen wird, ändert den Balken nicht;
es wird als LATE_EVENT protokolliert (neue Datenversion statt stiller Änderung).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from orderflow.dataadapter.events import MarketEvent
from orderflow.features.core import WindowStats, window_stats


@dataclass
class Bar:
    index: int
    start: int
    end: int
    available_at: int
    open: int | None = None
    high: int | None = None
    low: int | None = None
    close: int | None = None
    buy: int = 0
    sell: int = 0
    unknown: int = 0
    trade_count: int = 0
    complete: bool = True
    sealed: bool = False
    flags: set[str] = field(default_factory=set)
    _first_key: tuple[int, str, int] | None = None
    _last_key: tuple[int, str, int] | None = None

    @property
    def stats(self) -> WindowStats:
        return window_stats(self.buy, self.sell, self.unknown)

    @property
    def volume(self) -> int:
        return self.buy + self.sell + self.unknown

    def add(self, e: MarketEvent) -> None:
        assert not self.sealed
        p, s = e.price_ticks, e.size
        key = (e.exchange_ts, e.stream_id, e.sequence)
        if self._first_key is None or key < self._first_key:
            self._first_key, self.open = key, p
        if self._last_key is None or key > self._last_key:
            self._last_key, self.close = key, p
        self.high = p if self.high is None else max(self.high, p)
        self.low = p if self.low is None else min(self.low, p)
        if e.aggressor == "BUY":
            self.buy += s
        elif e.aggressor == "SELL":
            self.sell += s
        else:
            self.unknown += s
        self.trade_count += 1

    def summary(self) -> dict:
        st = self.stats
        return {
            "index": self.index,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "buy_volume": self.buy,
            "sell_volume": self.sell,
            "unknown_volume": self.unknown,
            "total_volume": st.total,
            "delta": st.delta,
            "known_fraction": None if st.known_fraction is None else float(st.known_fraction),
            "relative_delta": None if st.relative_delta is None else float(st.relative_delta),
            "complete": self.complete,
            "flags": sorted(self.flags | set(st.flags)),
        }


class BarBuilder:
    def __init__(self, window_start: int, window_end: int, bar_us: int, finalization_us: int):
        self.bar_us = bar_us
        self.window_start = window_start
        self.window_end = window_end
        n = (window_end - window_start) // bar_us
        self.bars = [
            Bar(i, window_start + i * bar_us, window_start + (i + 1) * bar_us,
                window_start + (i + 1) * bar_us + finalization_us)
            for i in range(n)
        ]
        self.late_events: list[MarketEvent] = []

    def index_of(self, exchange_ts: int) -> int | None:
        if not (self.window_start <= exchange_ts < self.window_end):
            return None
        return (exchange_ts - self.window_start) // self.bar_us

    def add_trade(self, e: MarketEvent) -> None:
        i = self.index_of(e.exchange_ts)
        if i is None:
            return
        bar = self.bars[i]
        if bar.sealed:
            self.late_events.append(e)
            return
        bar.add(e)

    def mark_incomplete(self, from_ts: int, to_ts: int, flag: str) -> None:
        """Markiert alle Balken, die [from_ts, to_ts] berühren, als unvollständig."""
        lo = max(from_ts, self.window_start)
        hi = min(to_ts, self.window_end - 1)
        if lo > hi:
            return
        for i in range(self.index_of(lo), self.index_of(hi) + 1):
            self.bars[i].complete = False
            self.bars[i].flags.add(flag)

    def seal(self, index: int) -> Bar:
        bar = self.bars[index]
        bar.sealed = True
        return bar
