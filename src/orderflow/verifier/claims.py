"""Begriffsprüfung freier Texte (Kap. 3, 9 Stufe A, T01, T07).

Feste Regelliste, keine semantische Bewertung. Ein Satz mit Negation (nicht/kein/unzulässig/
unbewiesen) gilt als zulässige Abgrenzung. Treffer sind Prüfbefunde, keine Wahrheitsurteile.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_RULES: list[tuple[str, re.Pattern[str]]] = [
    # Delta ist keine Differenz gekaufter und verkaufter Kontrakte: jeder Abschluss hat beide Seiten.
    ("DELTA_AS_NET_VOLUME", re.compile(
        r"(?<!aggressiv )(?<!aggressive )\bmehr\s+(?:ge)?kauft\w*\s+als\s+(?:ver)?kauft\w*\s+kontrakt"
        r"|\bmehr\s+käufer\s+als\s+verkäufer", re.I)),
    ("MANIPULATION_CLAIM", re.compile(r"\b(manipulation\w*|spoof\w*|manipuliert\w*)", re.I)),
    ("IDENTITY_CLAIM", re.compile(r"\b(institutionell\w*\s+(käufer|verkäufer|händler|teilnehmer)|smart\s+money)", re.I)),
    ("GUARANTEED_FILL", re.compile(r"\b(garantiert\w*\s+(fill|ausführung|gefüllt)|sicher\s+gefüllt|fill\s+ist\s+sicher)", re.I)),
    ("UNCALIBRATED_CONFIDENCE", re.compile(r"\b(confidence|konfidenz|sicherheit)\s*:?\s*\d{1,3}\s*%", re.I)),
]
_NEGATION = re.compile(r"\b(nicht|kein\w*|unzulässig\w*|unbewiesen\w*|nie)\b", re.I)


@dataclass(frozen=True)
class ClaimFinding:
    code: str
    sentence: str


def check_statement(text: str) -> list[ClaimFinding]:
    findings = []
    for sentence in re.split(r"(?<=[.!?;])\s+", text.strip()):
        if not sentence:
            continue
        for code, pattern in _RULES:
            if pattern.search(sentence) and not _NEGATION.search(sentence):
                findings.append(ClaimFinding(code, sentence))
    return findings
