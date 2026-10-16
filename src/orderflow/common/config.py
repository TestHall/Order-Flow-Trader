"""Eingefrorene, versionierte Konfiguration: Policy (Kap. 7.1) und Simulation (Kap. 8)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from orderflow.common.hashing import sha256_hex
from orderflow.common.paths import CONFIG_DIR


@dataclass(frozen=True)
class Policy:
    policy_version: str
    policy_hash: str
    timezone: str
    window_start: str
    window_end: str
    bar_seconds: int
    signal_start: str
    signal_end_exclusive: str
    reference_bars: int
    min_volume: int
    min_volume_multiple: Fraction
    min_known_fraction: Fraction
    min_relative_delta: Fraction
    breakout_min_ticks: int
    breakout_max_ticks: int
    confirmation_bars: int
    confirmation_offset_ticks: int
    invalidation_ticks: int
    stop_ticks_above_high: int
    min_distance_ticks: int
    max_distance_ticks: int
    budget_usd: Decimal
    max_contracts: int
    max_spread_ticks: int
    max_quote_age_ms: int
    signal_validity_ms: int
    target_multiple: int
    time_exit_minutes: int
    latest_exit_local: str
    max_candidates: int
    max_filled_entries: int
    cooldown_bars: int
    news_before_minutes: int
    news_after_minutes: int
    allow_long: bool


def load_policy(version: str = "OF-A-1.0") -> Policy:
    raw = (CONFIG_DIR / "policies" / f"{version}.toml").read_bytes()
    d = tomllib.loads(raw.decode("utf-8"))
    if d["policy_version"] != version:
        raise ValueError(f"Datei {version}.toml enthält policy_version {d['policy_version']}")
    if d.get("status") != "FROZEN":
        raise ValueError(f"Policy {version} ist nicht eingefroren")
    target_multiple = Fraction(d["exit"]["target_multiple"])
    if target_multiple.denominator != 1:
        raise ValueError("target_multiple muss ganzzahlig sein, damit Ziele auf dem Tickraster liegen")
    return Policy(
        policy_version=version,
        policy_hash=sha256_hex(raw),
        timezone=d["session"]["timezone"],
        window_start=d["session"]["window_start"],
        window_end=d["session"]["window_end"],
        bar_seconds=d["bars"]["length_seconds"],
        signal_start=d["signal_time"]["start"],
        signal_end_exclusive=d["signal_time"]["end_exclusive"],
        reference_bars=d["volume"]["reference_bars"],
        min_volume=d["volume"]["min_contracts"],
        min_volume_multiple=Fraction(d["volume"]["min_multiple_of_median"]),
        min_known_fraction=Fraction(d["classification"]["min_known_fraction"]),
        min_relative_delta=Fraction(d["delta"]["min_relative_delta"]),
        breakout_min_ticks=d["breakout"]["min_ticks_above_prior_high"],
        breakout_max_ticks=d["breakout"]["max_ticks_above_prior_high"],
        confirmation_bars=d["confirmation"]["bars"],
        confirmation_offset_ticks=d["confirmation"]["offset_ticks"],
        invalidation_ticks=d["invalidation"]["ticks_above_breakout_high"],
        stop_ticks_above_high=d["stop"]["ticks_above_breakout_high"],
        min_distance_ticks=d["risk"]["min_distance_ticks"],
        max_distance_ticks=d["risk"]["max_distance_ticks"],
        budget_usd=Decimal(d["risk"]["budget_per_idea_usd"]),
        max_contracts=d["risk"]["max_contracts"],
        max_spread_ticks=d["entry"]["max_spread_ticks"],
        max_quote_age_ms=d["entry"]["max_quote_age_ms"],
        signal_validity_ms=d["entry"]["signal_validity_ms"],
        target_multiple=int(target_multiple),
        time_exit_minutes=d["exit"]["time_exit_minutes"],
        latest_exit_local=d["exit"]["latest_exit_local"],
        max_candidates=d["limits"]["max_candidates_per_session"],
        max_filled_entries=d["limits"]["max_filled_entries_per_session"],
        cooldown_bars=d["cooldown"]["bars_after_rejection"],
        news_before_minutes=d["news"]["blackout_before_minutes"],
        news_after_minutes=d["news"]["blackout_after_minutes"],
        allow_long=d["direction"] != "SHORT_ONLY",
    )


@dataclass(frozen=True)
class SimConfig:
    simulation_version: str
    config_hash: str
    roundtrip_fee_usd: Decimal
    sizing_slippage_reserve_ticks: int
    extra_slippage_ticks: int
    order_latency_ms: int
    exit_latency_ms: int
    bar_finalization_ms: int
    assumed_receive_latency_ms: int
    starting_capital_usd: Decimal

    def with_overrides(self, **kw) -> "SimConfig":
        from dataclasses import replace

        return replace(self, **kw)


def load_sim_config(version: str = "SIM-1.0") -> SimConfig:
    raw = (CONFIG_DIR / "simulation" / f"{version}.toml").read_bytes()
    d = tomllib.loads(raw.decode("utf-8"))
    if d.get("cost_approach") != "REALISTIC_FILLS":
        raise ValueError("Nur Kostenansatz REALISTIC_FILLS ist implementiert (Kap. 8.2)")
    return SimConfig(
        simulation_version=d["simulation_version"],
        config_hash=sha256_hex(raw),
        roundtrip_fee_usd=Decimal(d["costs"]["roundtrip_fee_usd"]),
        sizing_slippage_reserve_ticks=d["costs"]["sizing_slippage_reserve_ticks"],
        extra_slippage_ticks=d["costs"]["extra_slippage_ticks"],
        order_latency_ms=d["latency"]["order_latency_ms"],
        exit_latency_ms=d["latency"]["exit_latency_ms"],
        bar_finalization_ms=d["replay"]["bar_finalization_ms"],
        assumed_receive_latency_ms=d["replay"]["assumed_receive_latency_ms"],
        starting_capital_usd=Decimal(d["account"]["starting_capital_usd"]),
    )
