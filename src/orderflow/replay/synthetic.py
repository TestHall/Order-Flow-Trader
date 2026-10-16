"""Synthetische Sitzungen für Selbsttests und deterministische Replays (Kap. 16.3 Schritt 1).

Alle hier erzeugten Ereignisse sind künstlich und enthalten keine echten Marktdaten. Sie werden
als Rohdatensätze nach schemas/market_event.schema.json erzeugt und laufen durch denselben
Datenadapter wie spätere reale Daten.
"""
from __future__ import annotations

import bisect
import random
from dataclasses import dataclass, field
from datetime import date

from orderflow.common.config import Policy, SimConfig
from orderflow.common.timeutil import MINUTE, MS, SECOND, format_ts, local_to_us
from orderflow.dataadapter.events import MarketEvent, normalize_all
from orderflow.features.session import NewsCalendar, SessionContext

INSTRUMENT = "MES_TEST"
TRADES = "trades-A"
QUOTES = "quotes-A"
STATUS = "status-A"


@dataclass
class _Trade:
    ts: int
    price: int
    size: int
    aggressor: str
    receive_delay_ms: int | None = None
    classification: str = "EXCHANGE"


@dataclass
class SyntheticSession:
    day: date
    policy: Policy
    receive_latency_ms: int = 20
    quote_heartbeat_ms: int = 1000
    quote_size: int = 20
    tag: str = "A"
    trades: list[_Trade] = field(default_factory=list)
    explicit_quotes: list[tuple[int, int, int, int, int]] = field(default_factory=list)
    statuses: list[tuple[int, str, str | None]] = field(default_factory=list)
    heartbeat_off: list[tuple[int, int]] = field(default_factory=list)
    dense_quotes: list[tuple[int, int, int]] = field(default_factory=list)
    dropped: list[tuple[str, int, int]] = field(default_factory=list)
    duplicates: list[tuple[str, int]] = field(default_factory=list)
    receive_overrides: dict[tuple[str, int], int] = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        return f"{INSTRUMENT}-{self.day.isoformat()}-{self.tag}"

    # --------------------------------------------------------- Zeit
    def at(self, hhmmss: str) -> int:
        """Lokale New-York-Zeit 'HH:MM[:SS[.fff]]' dieses Tages -> UTC-Mikrosekunden."""
        parts = hhmmss.split(":")
        hh, mm = parts[0], parts[1]
        sec = float(parts[2]) if len(parts) > 2 else 0.0
        base = local_to_us(self.day, f"{hh}:{mm}", self.policy.timezone)
        return base + int(round(sec * 1000)) * MS

    @property
    def window(self) -> tuple[int, int]:
        return (local_to_us(self.day, self.policy.window_start, self.policy.timezone),
                local_to_us(self.day, self.policy.window_end, self.policy.timezone))

    # ------------------------------------------------------ Bausteine
    def trade(self, ts: int, price: int, size: int, aggressor: str, receive_delay_ms: int | None = None,
              classification: str | None = None) -> None:
        cls = classification or ("NONE" if aggressor == "UNKNOWN" else "EXCHANGE")
        self.trades.append(_Trade(ts, price, size, aggressor, receive_delay_ms, cls))

    def quote(self, ts: int, bid: int, ask: int, bid_size: int | None = None, ask_size: int | None = None) -> None:
        self.explicit_quotes.append((ts, bid, ask, self.quote_size if bid_size is None else bid_size,
                                     self.quote_size if ask_size is None else ask_size))

    def status(self, ts: int, market_status: str, text: str | None = None) -> None:
        self.statuses.append((ts, market_status, text))

    def no_heartbeat(self, start: int, end: int) -> None:
        self.heartbeat_off.append((start, end))

    def dense(self, start: int, end: int, interval_ms: int = 100) -> None:
        """Zusätzliche Wiederholungs-Quotes im Abstand interval_ms (realistischere Quote-Dichte)."""
        self.dense_quotes.append((start, end, interval_ms))

    def drop(self, stream: str, start: int, end: int) -> None:
        """Entfernt Ereignisse eines Streams nach Sequenzvergabe -> Sequenzlücke."""
        self.dropped.append((stream, start, end))

    def duplicate(self, stream: str, ts: int) -> None:
        self.duplicates.append((stream, ts))

    def bar(self, minute_start: int, prices: list[int], buy: int, sell: int, unknown: int = 0) -> None:
        """Ein Balken mit Trades zu den gegebenen Preisen (in Zeitreihenfolge), Volumen B/S/U.

        Die Größen werden gleichmäßig auf die Preise verteilt; die Aggressorseite wechselt so,
        dass genau B, S und U entstehen."""
        sides = (["BUY"] * buy) + (["SELL"] * sell) + (["UNKNOWN"] * unknown)
        n = len(prices)
        if n == 0:
            return
        chunks: list[dict[str, int]] = [dict() for _ in range(n)]
        for k, side in enumerate(sides):
            c = chunks[k % n]
            c[side] = c.get(side, 0) + 1
        step = 60 * SECOND // (n + 1)
        for i, (price, c) in enumerate(zip(prices, chunks)):
            for j, (side, size) in enumerate(sorted(c.items())):
                self.trade(minute_start + (i + 1) * step + j * MS, price, size, side)

    def quiet_bars(self, first_minute: int, count: int, low: int, high: int, volume: int = 120) -> None:
        """Ausgeglichene Balken (B = S) unterhalb eines Levels, deterministisch."""
        span = max(high - low, 0)
        for k in range(count):
            start = first_minute + k * MINUTE
            prices = [low + ((k + i) % (span + 1)) for i in range(6)]
            self.bar(start, prices, volume // 2, volume - volume // 2)

    # ---------------------------------------------------------- Bauen
    def _quote_changes(self) -> list[tuple[int, int, int, int, int]]:
        changes = []
        for tr in self.trades:
            if tr.aggressor == "BUY":
                bid, ask = tr.price - 1, tr.price
            else:
                bid, ask = tr.price, tr.price + 1
            changes.append((tr.ts + MS, bid, ask, self.quote_size, self.quote_size))
        changes.extend(self.explicit_quotes)
        changes.sort()
        return changes

    def records(self) -> list[dict]:
        lo, hi = self.window
        changes = self._quote_changes()
        change_ts = [c[0] for c in changes]
        quotes = list(changes)
        if self.quote_heartbeat_ms > 0 and changes:
            t = lo - MINUTE
            step = self.quote_heartbeat_ms * MS
            explicit_ts = {q[0] for q in self.explicit_quotes}
            while t < hi:
                off = any(a <= t < b for a, b in self.heartbeat_off)
                i = bisect.bisect_right(change_ts, t) - 1
                if not off and i >= 0 and t not in explicit_ts:
                    _, bid, ask, bs, as_ = changes[i]
                    quotes.append((t, bid, ask, bs, as_))
                t += step
            for a, b, ms in self.dense_quotes:
                t = a
                while t < b:
                    off = any(x <= t < y for x, y in self.heartbeat_off)
                    i = bisect.bisect_right(change_ts, t) - 1
                    if not off and i >= 0 and t not in explicit_ts and (t - (lo - MINUTE)) % step:
                        _, bid, ask, bs, as_ = changes[i]
                        quotes.append((t, bid, ask, bs, as_))
                    t += ms * MS
        quotes = sorted(set(quotes))

        raw: list[tuple[str, int, dict]] = []
        for tr in sorted(self.trades, key=lambda x: x.ts):
            raw.append((TRADES, tr.ts, {
                "event_type": "TRADE", "price_ticks": tr.price, "size_contracts": tr.size,
                "aggressor": tr.aggressor, "classification_method": tr.classification,
                "_delay": tr.receive_delay_ms}))
        for ts, bid, ask, bs, as_ in quotes:
            raw.append((QUOTES, ts, {"event_type": "QUOTE", "bid_ticks": bid, "ask_ticks": ask,
                                     "bid_size": bs, "ask_size": as_}))
        for ts, st, text in sorted(self.statuses):
            body = {"event_type": "STATUS", "market_status": st}
            if text is not None:
                body["text"] = text
            raw.append((STATUS, ts, body))

        def receive_of(stream: str, ts: int, body: dict) -> int:
            delay = body.get("_delay")
            delay = self.receive_latency_ms if delay is None else delay
            return self.receive_overrides.get((stream, ts), ts + delay * MS)

        out = []
        seq: dict[str, int] = {}
        # Sequenznummern in Veröffentlichungsreihenfolge (Empfangszeit), wie in einem echten Kanal:
        # eine verspätet veröffentlichte Meldung trägt eine spätere Sequenz, aber frühere Börsenzeit.
        for stream, ts, body in sorted(raw, key=lambda r: (r[0], receive_of(*r), r[1])):
            seq[stream] = seq.get(stream, 0) + 1
            receive = receive_of(stream, ts, body)
            body.pop("_delay", None)
            rec = {
                "schema_version": "1.0",
                "event_id": f"{self.session_id}-{stream}-{seq[stream]}",
                "venue": "CME",
                "instrument_id": INSTRUMENT,
                "session_id": self.session_id,
                "stream_id": stream,
                "sequence": seq[stream],
                "exchange_ts": format_ts(ts),
                "receive_ts": format_ts(receive),
                **body,
                "source_hash": f"synthetic-{self.session_id}",
            }
            if any(s == stream and a <= ts < b for s, a, b in self.dropped):
                continue
            out.append(rec)
            if (stream, ts) in self.duplicates:
                out.append(dict(rec))
        return out

    def events(self, sim: SimConfig) -> list[MarketEvent]:
        return normalize_all(self.records(), sim.assumed_receive_latency_ms)

    def context(self, prior_high_ticks: int | None, news: NewsCalendar | None = NewsCalendar(()),
                not_evaluable_reason: str | None = None) -> SessionContext:
        lo, hi = self.window
        return SessionContext(self.session_id, INSTRUMENT, self.day, lo, hi, prior_high_ticks, news,
                              not_evaluable_reason)


def random_session(day: date, policy: Policy, seed: int, prior_high: int = 20000) -> SyntheticSession:
    """Zufällige, aber reproduzierbare Sitzung um ein Vortageshoch herum.

    Erzeugt ruhige Phasen, gelegentliche Ausbrüche über P mit wechselnder Fortsetzung (Rückkehr,
    Widerlegung, Seitwärts) und optional Datenstörungen. Dient Eigenschaftstests (Kap. 10.4) und
    Gesamtsitzungsläufen (Kap. 16.3 Schritt 3); er ist kein Marktmodell."""
    rng = random.Random(seed)
    s = SyntheticSession(day, policy, tag=f"R{seed}")
    lo, hi = s.window
    minutes = (hi - lo) // MINUTE
    price = prior_high - rng.randint(4, 12)
    k = 0
    while k < minutes:
        start = lo + k * MINUTE
        local_min = k + 30  # Minuten seit 09:00
        roll = rng.random()
        if local_min >= 60 and roll < 0.06 and price <= prior_high:
            # Ausbruchsbalken
            high = prior_high + rng.randint(1, 9)
            close = prior_high + rng.randint(-1, max(0, high - prior_high - 1))
            low = min(price, prior_high) - rng.randint(0, 2)
            buy = rng.randint(120, 220)
            sell = rng.randint(50, 130)
            unk = rng.choice([0, 0, 0, 5, 40])
            s.bar(start, [low, (low + high) // 2, high, close], buy, sell, unk)
            s.dense(start + MINUTE, min(start + 16 * MINUTE, hi))  # dichte Quotes für Entry/Exit
            price = close
            k += 1
            mode = rng.choice(["return", "return", "barrier", "drift"])
            for _ in range(rng.randint(2, 6)):
                if k >= minutes:
                    break
                start = lo + k * MINUTE
                if mode == "return":
                    price -= rng.randint(1, 4)
                elif mode == "barrier":
                    price += rng.randint(1, 3)
                else:
                    price += rng.choice([-1, 0, 1])
                s.bar(start, [price + 1, price + 2, price - 1, price], 60, 60)
                k += 1
            continue
        drift = rng.choice([-2, -1, 0, 1, 1, 2]) if price < prior_high - 2 else rng.choice([-3, -2, -1, 0])
        price += drift
        vol = rng.randint(80, 160)
        s.bar(start, [price, price + 1, price - 1, price], vol // 2, vol - vol // 2,
              rng.choice([0, 0, 0, 0, 2]))
        k += 1
    # Störungen deterministisch je Seed, damit jede Art in kleinen Seed-Mengen vorkommt (Kap. 8.3).
    if seed % 4 == 0:
        gap_at = lo + rng.randint(60, minutes - 30) * MINUTE
        s.drop(TRADES, gap_at, gap_at + 20 * SECOND)
    if seed % 5 == 0:
        s.duplicate(TRADES, rng.choice(s.trades).ts)
    if seed % 6 == 0:
        halt_at = lo + rng.randint(60, minutes - 30) * MINUTE + 15 * SECOND
        s.status(halt_at, "HALT")
        s.status(halt_at + rng.randint(2, 6) * MINUTE, "OPEN")
    return s
