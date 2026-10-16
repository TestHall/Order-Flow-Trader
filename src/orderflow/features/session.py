"""Sitzungszuordnung über Zeitzonendaten und Börsenkalender (Kap. 4.1, T19) sowie Nachrichtenkalender.

Es gibt keine pauschale UTC-Uhrzeit: das Referenzfenster wird je Kalendertag aus America/New_York
abgeleitet. Ist ein Tag nicht vom Kalender abgedeckt, ist die Sitzung nicht prüfbar.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from orderflow.common.timeutil import MINUTE, local_to_us, us_to_local
from orderflow.dataadapter.events import MarketEvent


class CalendarUnavailable(LookupError):
    pass


@dataclass(frozen=True)
class TradingCalendar:
    calendar_id: str
    timezone: str
    covers_from: date
    covers_to: date
    closed: frozenset[date]
    early_close: dict[date, str] = field(hash=False, default_factory=dict)
    synthetic: bool = False

    @staticmethod
    def load(path: Path) -> "TradingCalendar":
        d = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        return TradingCalendar(
            calendar_id=d["calendar_id"],
            timezone=d["timezone"],
            covers_from=date.fromisoformat(d["covers_from"]),
            covers_to=date.fromisoformat(d["covers_to"]),
            closed=frozenset(date.fromisoformat(x["date"]) for x in d.get("closed", [])),
            early_close={date.fromisoformat(x["date"]): x["close"] for x in d.get("early_close", [])},
            synthetic=d.get("synthetic", False),
        )

    def covers(self, day: date) -> bool:
        return self.covers_from <= day <= self.covers_to

    def is_trading_day(self, day: date) -> bool:
        if not self.covers(day):
            raise CalendarUnavailable(f"{self.calendar_id} deckt {day} nicht ab")
        return day.weekday() < 5 and day not in self.closed

    def reference_window(self, day: date, start: str, end: str) -> tuple[int, int]:
        if not self.is_trading_day(day):
            raise CalendarUnavailable(f"{day} ist kein Handelstag in {self.calendar_id}")
        close = self.early_close.get(day)
        if close is not None and close < end:
            end = close
        return local_to_us(day, start, self.timezone), local_to_us(day, end, self.timezone)

    def session_of(self, ts: int, start: str, end: str) -> date | None:
        """Ordnet einen UTC-Zeitstempel dem Referenzfenster seines lokalen Kalendertags zu."""
        day = us_to_local(ts, self.timezone).date()
        if not self.is_trading_day(day):
            return None
        lo, hi = self.reference_window(day, start, end)
        return day if lo <= ts < hi else None

    def previous_trading_day(self, day: date) -> date:
        d = day - timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d


@dataclass(frozen=True)
class NewsEvent:
    scheduled_at: int
    known_since: int
    label: str


@dataclass(frozen=True)
class NewsCalendar:
    events: tuple[NewsEvent, ...]

    def blackout(self, interval_start: int, interval_end: int, decision_ts: int,
                 before_min: int, after_min: int) -> list[NewsEvent]:
        """Angekündigte Termine (zum Entscheidungszeitpunkt bekannt), deren Sperrfenster
        [Termin - before, Termin + after] das Intervall [interval_start, interval_end] berührt."""
        hits = []
        for n in self.events:
            if n.known_since > decision_ts:
                continue
            lo = n.scheduled_at - before_min * MINUTE
            hi = n.scheduled_at + after_min * MINUTE
            if lo <= interval_end and interval_start <= hi:
                hits.append(n)
        return hits


def prior_reference_high(events: list[MarketEvent], window: tuple[int, int]) -> int | None:
    """Höchstes gültiges Trade-Preisniveau im vorherigen Referenzfenster (Kap. 4.1).

    Gültig = TRADE im Fenster, nicht durch TRADE_BUST annulliert; TRADE_CORRECTION ersetzt den Preis.
    Ohne Trades -> None (dann kein Signal).
    """
    lo, hi = window
    prices: dict[str, int] = {}
    for e in events:
        if e.event_type == "TRADE" and lo <= e.exchange_ts < hi:
            prices[e.event_id] = e.price_ticks
    for e in events:
        if e.event_type == "TRADE_BUST":
            prices.pop(e.corrects_event_id, None)
        elif e.event_type == "TRADE_CORRECTION" and e.corrects_event_id in prices:
            prices[e.corrects_event_id] = e.price_ticks
    return max(prices.values()) if prices else None


@dataclass(frozen=True)
class SessionContext:
    """Alles, was vor Sitzungsbeginn über die Sitzung bekannt ist."""
    session_id: str
    instrument_id: str
    day: date
    window_start: int
    window_end: int
    prior_high_ticks: int | None          # Vortageshoch P; None -> kein Signal
    news: NewsCalendar | None             # None -> Kalender fehlt -> Sitzung nicht prüfbar
    not_evaluable_reason: str | None = None

    @property
    def evaluable(self) -> bool:
        return self.news is not None and self.not_evaluable_reason is None
