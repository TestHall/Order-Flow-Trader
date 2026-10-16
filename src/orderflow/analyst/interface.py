"""Vertrag des KI-Analysten (Kap. 13.1–13.3, 15, 16.2).

Der Analyst erhält ausschließlich eine freigegebene, schreibgeschützte Sicht auf bereits
protokollierte Entscheidungen, Snapshots, Ergebnisse und Prüferbefunde eines Lernabschnitts.
Er liefert Lernprotokolle nach schemas/review.schema.json. Ein Lernprotokoll ändert keine aktive
Policy; Übernahmen laufen nur über das Versionsverfahren (Kap. 13.5, 17.1).
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence

from orderflow.common import schema
from orderflow.journal.journal import Journal

CAPABILITY_MISSING = "CAPABILITY_MISSING"
RELEASED_KINDS = ("SNAPSHOT", "DECISION", "RESULT", "UNSCORABLE", "EXIT_TRIGGER")


@dataclass(frozen=True)
class AnalystView:
    """Schreibgeschützte Sicht. Prüferbefunde werden vom Betreiber übergeben, nie vom Analysten gelesen."""
    session_id: str
    records: tuple[Mapping[str, Any], ...]
    verifier_findings: tuple[Mapping[str, Any], ...]

    @staticmethod
    def from_journal(session_id: str, journal: Journal,
                     verifier_findings: Sequence[Mapping[str, Any]] = ()) -> "AnalystView":
        recs = tuple(MappingProxyType(dict(r)) for r in journal.records() if r["kind"] in RELEASED_KINDS)
        return AnalystView(session_id, recs, tuple(MappingProxyType(dict(f)) for f in verifier_findings))


class Analyst(Protocol):
    def review(self, view: AnalystView) -> list[dict[str, Any]]: ...


def accept_reviews(reviews: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Validiert Lernprotokolle. Ungültige werden mit Grund abgewiesen, nie stillschweigend repariert."""
    ok, rejected = [], []
    for i, r in enumerate(reviews):
        errs = schema.errors(r, "review")
        if errs:
            rejected.append(f"review[{i}]: " + "; ".join(errs))
        elif r["status"] != "PROPOSED":
            rejected.append(f"review[{i}]: Analyst darf nur PROPOSED setzen; Status vergibt der Prüfer")
        else:
            ok.append(r)
    return ok, rejected
