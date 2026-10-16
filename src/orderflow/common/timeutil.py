"""Zeitstempel als ganze Mikrosekunden seit Epoche (UTC)."""
from __future__ import annotations

import re
from functools import lru_cache
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z$")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

US = 1
MS = 1_000
SECOND = 1_000_000
MINUTE = 60 * SECOND


@lru_cache(maxsize=4096)
def _day_us(day: str) -> int:
    return (datetime.fromisoformat(day).replace(tzinfo=timezone.utc) - _EPOCH) // timedelta(microseconds=1)


def parse_ts(value: str) -> int:
    """Parst einen UTC-Zeitstempel mit Suffix Z. Andere Zeitzonen werden abgewiesen (Kap. 4.2)."""
    m = _TS.match(value)
    if not m:
        raise ValueError(f"Kein UTC-Zeitstempel mit Z-Suffix: {value!r}")
    hh, mm, ss = int(value[11:13]), int(value[14:16]), int(value[17:19])
    if hh > 23 or mm > 59 or ss > 59:
        raise ValueError(f"Ungültige Uhrzeit: {value!r}")
    frac = (m.group(2) or "").ljust(9, "0")
    if int(frac[6:]) != 0:
        raise ValueError(f"Auflösung feiner als Mikrosekunden wird nicht unterstützt: {value!r}")
    return _day_us(value[:10]) + ((hh * 60 + mm) * 60 + ss) * SECOND + int(frac[:6])


def format_ts(us: int) -> str:
    dt = _EPOCH + timedelta(microseconds=us)
    frac = us % SECOND
    if frac % MS == 0:
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{frac // MS:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{frac:06d}Z"


def local_to_us(day: date, hhmm: str | time, tz: str) -> int:
    """Lokale Uhrzeit eines Kalendertags -> UTC-Mikrosekunden über Zeitzonendaten (Sommerzeit, Kap. 4.1)."""
    t = hhmm if isinstance(hhmm, time) else time.fromisoformat(hhmm)
    dt = datetime.combine(day, t, tzinfo=ZoneInfo(tz))
    return (dt - _EPOCH) // timedelta(microseconds=1)


def us_to_local(us: int, tz: str) -> datetime:
    return (_EPOCH + timedelta(microseconds=us)).astimezone(ZoneInfo(tz))
