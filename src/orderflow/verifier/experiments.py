"""Experimentregister und Datenzeiträume (Kap. 12.1, 12.4, 14.3, 17.1, T20).

Ein bereits wiederholt angesehener Zeitraum liefert keinen neuen unabhängigen Nachweis. Er wird
als Validierungsdaten verbraucht markiert; für einen unabhängigen Test sind neue, spätere
Holdout-Daten zu reservieren.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class DataPeriod:
    period_id: str
    start: str
    end: str
    role: str                    # DEVELOPMENT | VALIDATION | HOLDOUT
    status: str = "UNTOUCHED"    # UNTOUCHED | VIEWED | CONSUMED_AS_VALIDATION
    views: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ClaimAssessment:
    candidate_id: str
    period_id: str
    independent_evidence: bool
    finding: str                 # VALIDATED_WITH_SCOPE | REJECTED | INCONCLUSIVE
    reason: str
    new_holdout_required: bool


class ExperimentRegistry:
    def __init__(self, path: Path | None = None, attempt_budget: int = 3):
        self.path = path
        self.attempt_budget = attempt_budget
        self.periods: dict[str, DataPeriod] = {}
        self.attempts: list[dict] = []
        if path is not None and Path(path).exists():
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            self.attempt_budget = d["attempt_budget"]
            self.periods = {p["period_id"]: DataPeriod(**p) for p in d["periods"]}
            self.attempts = d["attempts"]

    def save(self) -> None:
        if self.path is None:
            return
        Path(self.path).write_text(json.dumps({
            "attempt_budget": self.attempt_budget,
            "periods": [asdict(p) for p in self.periods.values()],
            "attempts": self.attempts,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def add_period(self, period_id: str, start: str, end: str, role: str) -> DataPeriod:
        if role not in ("DEVELOPMENT", "VALIDATION", "HOLDOUT"):
            raise ValueError(role)
        s, e = date.fromisoformat(start), date.fromisoformat(end)
        for p in self.periods.values():
            if not (e < date.fromisoformat(p.start) or s > date.fromisoformat(p.end)):
                raise ValueError(f"{period_id} überlappt {p.period_id}")
        period = DataPeriod(period_id, start, end, role)
        self.periods[period_id] = period
        self.save()
        return period

    def record_view(self, period_id: str, candidate_id: str, purpose: str) -> None:
        p = self.periods[period_id]
        p.views.append({"candidate_id": candidate_id, "purpose": purpose})
        if p.status == "UNTOUCHED":
            p.status = "VIEWED"
        self.save()

    def register_attempt(self, candidate_id: str, change: str) -> int:
        if any(a["candidate_id"] == candidate_id for a in self.attempts):
            raise ValueError(f"{candidate_id} bereits registriert")
        if len(self.attempts) >= self.attempt_budget:
            raise RuntimeError("Versuchsbudget erschöpft: bilanzieren statt weiter suchen (Kap. 14.3)")
        self.attempts.append({"candidate_id": candidate_id, "change": change})
        self.save()
        return len(self.attempts)

    def assess_claim(self, candidate_id: str, period_id: str, improved: bool) -> ClaimAssessment:
        p = self.periods[period_id]
        reused = len(p.views) > 1 or p.status == "CONSUMED_AS_VALIDATION" or p.role != "HOLDOUT"
        if reused:
            p.role = "VALIDATION"
            p.status = "CONSUMED_AS_VALIDATION"
            self.save()
            return ClaimAssessment(candidate_id, period_id, False, "INCONCLUSIVE",
                                   "Zeitraum bereits wiederholt angesehen: kein neuer unabhängiger Nachweis",
                                   new_holdout_required=True)
        p.status = "CONSUMED_AS_VALIDATION"  # nach Sichtung nicht mehr geheim (Kap. 14.3)
        self.save()
        return ClaimAssessment(candidate_id, period_id, True,
                               "VALIDATED_WITH_SCOPE" if improved else "REJECTED",
                               "Erstmalige Prüfung auf unberührtem Holdout", new_holdout_required=True)

    def reserve_holdout(self, period_id: str, start: str, end: str) -> DataPeriod:
        latest_used = max((date.fromisoformat(p.end) for p in self.periods.values()), default=None)
        if latest_used is not None and date.fromisoformat(start) <= latest_used:
            raise ValueError("Neuer Holdout muss nach allen bisher genutzten Zeiträumen liegen")
        return self.add_period(period_id, start, end, "HOLDOUT")
