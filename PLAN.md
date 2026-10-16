# Order-Flow-Agent — Bauplan für Claude Code

Grundlage: `KI-Order-Flow-Ausbildung.md` (Ausbildungs- und Prüfhandbuch, Version 1.0, 23.09.2026).
Entscheidungen (bestätigt): volles System inkl. Shadow-Modus als Zielumfang; Python-Backend; historische MES-Daten für Schritt 1–2 noch nicht beschafft (Start daher mit synthetischen Testfällen T01–T20).

Dieses Dokument ist als Arbeitsgrundlage für eine Claude-Code-Session gedacht: jeder Meilenstein ist so geschnitten, dass er als ein Arbeitsauftrag an Claude Code übergeben werden kann (Kontext, Akzeptanzkriterien, Testfälle aus dem Handbuch).

---

## 1. Zielsetzung & Nicht-Ziele

**Ziel:** Ein Python-System, das den KI-Agenten aus dem Handbuch technisch umsetzt — von synthetischen Selbsttests über eine deterministische Baseline-Strategie (OF-A-1.0) bis zu Holdout-Prüfung und Shadow-Beobachtung mit echten Marktdaten. Ausführung **ohne echte Orders** (REPLAY/SHADOW-Modus, Kap. 1.2 und 14.4 des Handbuchs).

**Nicht-Ziel in dieser Phase:** Live-Anbindung an einen Broker, echte Order-Ausführung, Profitabilitätszusage. Das Handbuch selbst schließt das für Version 1.0 aus (Kap. 1.2, 14).

---

## 2. Architektur — 7 Komponenten (Kap. 2 des Handbuchs)

| Komponente | Verantwortung | Python-Modul (Vorschlag) |
| --- | --- | --- |
| Datenadapter | Marktereignisse normalisieren, Herkunft anhängen | `dataadapter/` |
| Merkmalsberechnung | Delta, Levels, Zeitfenster, Qualitätsflags — deterministisch | `features/` |
| Regelmodul | Zustandsautomat, erlaubte Aktionen (`IDLE`→`ARMED`→…) | `rules/` |
| KI-Analyst | Erklärt Beobachtungen, schlägt Änderungen vor — darf **keine** Preise/Fills festlegen | `analyst/` (LLM-Aufrufe nur hier) |
| Risiko & Ausführung | Positionsgrößen, Orderstatus, Fill-Simulation | `execution/` |
| Externer Prüfer | Bewertet Entscheidungen unabhängig, feste Regeln | `verifier/` |
| Versionsverwaltung | Append-only Journal für Decisions/Orders/Fills | `journal/` |

Wichtig aus dem Handbuch: Die Trennung ist eine harte Vorgabe, kein Implementierungsdetail — der KI-Analyst darf laut Kap. 2 Tabelle **nicht** Preise, Fills oder Prüfergebnisse selbst festlegen, und der Prüfer **nicht** dem Kandidaten verdeckte Lösungen vor der Entscheidung zeigen. Diese Zugriffsgrenzen sollten als getrennte Python-Packages mit expliziten Interfaces erzwungen werden, nicht nur als Konvention.

---

## 3. Repo-Struktur (Vorschlag)

```
order-flow-agent/
├── PLAN.md                     ← dieses Dokument
├── docs/
│   └── KI-Order-Flow-Ausbildung.md   ← Handbuch als Spec-Referenz
├── src/
│   ├── dataadapter/
│   ├── features/
│   ├── rules/
│   ├── analyst/
│   ├── execution/
│   ├── verifier/
│   └── journal/
├── schemas/                     ← JSON-Schemas aus Kap. 5 (Marktereignis, Snapshot, Decision)
├── tests/
│   ├── synthetic/               ← T01–T20 aus Kap. 10
│   └── invariants/              ← Zustandsautomat-Invarianten Kap. 7.5
├── experiments/                 ← Vorregistrierungen Kap. 17.1, ein Ordner je Experiment
└── reports/                     ← Abschlussberichte Kap. 17.2
```

---

## 4. Datenverträge zuerst (Kap. 5)

Bevor Logik entsteht: JSON-Schema für **Marktereignis**, **Snapshot** und **Decision** aus Kap. 5.1–5.3 als `schemas/*.json` festschreiben (Pflichtfelder, Enums, Wertebereiche — z. B. `aggressor ∈ {BUY, SELL, UNKNOWN}`, `price_ticks` als Integer). Das schafft die Grundlage, gegen die alle späteren Module validieren, und macht T01–T20 direkt maschinell prüfbar.

---

## 5. Referenzstrategie OF-A-1.0 (Kap. 7) — als Konfiguration, nicht Hardcode

Alle Parameter aus der Tabelle in Kap. 7.1 (Mindestvolumen, relatives Delta ≥ +0,20, Ausdehnung 1–8 Ticks über Vortageshoch, Stoppabstand 2 Ticks, Ziel = 2×Risiko, Zeitausstieg 10 Min. etc.) gehören in eine versionierte Konfigurationsdatei (`policy_version: OF-A-1.0`), nie in Code-Konstanten — das Handbuch verlangt ausdrücklich, dass Parameter vor jedem Versuch eingefroren und versioniert werden (Kap. 1).

---

## 6. Meilensteinplan (erweitert aus Kap. 16.3 auf vollen Umfang inkl. Shadow-Modus)

Jeder Meilenstein = ein möglicher Claude-Code-Auftrag mit klarem "Definition of Done".

**M1 — Schemas & synthetische Selbsttests (kein Marktdatenzugriff nötig)**
Schemas aus Kap. 5 implementieren. T01–T20 (Kap. 10) plus Zustandsinvarianten (Kap. 7.5) als automatisierte Tests schreiben, inkl. Grenzfälle (T09–T14: Bestätigung, Widerlegung zuerst, Fristablauf, veraltete Quote, zu späte KI-Antwort).
*DoD:* Maschinenlesbare PASS/FAIL-Datei mit Ist/Soll-Werten (Kap. 16.3 Schritt 1). Keine Marktleistungsaussage.

**M2 — Deterministisches Regelmodul & Fill-Simulator**
Zustandsautomat (`IDLE…HALT_NEW_ENTRIES`), Kandidaten-/Bestätigungs-/Verwerfungslogik (Kap. 7.2–7.4), Fill-Simulator ohne fiktive Fills (Kap. 8.2 — Verkauf nur zum sichtbaren Bid, Rückkauf nur zum sichtbaren Ask).
*DoD:* Vollständige Sitzungen laufen deterministisch und reproduzierbar durch, inkl. `NO_TRADE`-Fälle und Ausfällen (Kap. 16.3 Schritt 3).

**M3 — Datenadapter für reale MES-Daten (blockiert auf Datenquelle, siehe Abschnitt 8)**
Sobald eine Quelle steht: Pflichtprüfungen aus Kap. 4.2 (Preisraster, Sequenzlücken, Zeitzonen, veraltete Quotes) implementieren.
*DoD:* Datenqualitätsbericht für eine kleine reale Stichprobe (Kap. 16.3 Schritt 2).

**M4 — KI-Analyst als Diagnosewerkzeug (noch kein Entry-Filter)**
LLM-Komponente, die Regelvollzugsfehler erkennt/erklärt, gegen Prüferlabels gemessen (Kap. 16.3 Schritt 4). Das Handbuch verlangt, diesen Nutzen zuerst separat zu belegen, bevor die KI überhaupt einen Entry beeinflusst.
*DoD:* Messbare Diagnosequalität vs. Baseline ohne LLM.

**M5 — Optionaler KI-Filter als eigener Kandidat**
KI-Ausgabe filtert Einstiege; reale Antwortlatenz und Kosten fließen in den Vergleich ein (Kap. 2.1, 16.3 Schritt 5). Sonderfall `NO_TRADE_EXPIRED` bei zu später Antwort (Kap. 2.1, T14) beachten.
*DoD:* Vergleichsbericht Kandidat vs. unveränderte Baseline, inkl. Kosten.

**M6 — Backtest-Framework ohne Datenleck**
Walk-forward-Split, Baselines, Ablationen, Block-Bootstrap-Konfidenzintervall (Kap. 12).
*DoD:* Abschlussbericht nach Vorlage Kap. 17.2 inkl. Unsicherheitsintervall.

**M7 — Holdout & Shadow-Modus (Zielumfang lt. deiner Auswahl)**
Verdeckte Prüfung auf eingefrorenem Zeitabschnitt (Kap. 9 Stufe F), danach Shadow-Beobachtung mit neuen Live-Daten ohne echte Orders; Überwachung von Datenqualität, Gate-Verletzungen, Nettokosten (Kap. 14.4).
*DoD:* Shadow-Bericht mit Gültigkeitsbereich — ausdrücklich **keine** automatische Live-Freigabe (Kap. 9 Stufe F, Kap. 14.2).

---

## 7. Reihenfolge-Prinzip

M1 und M2 brauchen keine echten Marktdaten und können sofort starten. M3 (reale Daten) läuft parallel zur Klärung der Datenquelle/Lizenz (Abschnitt 8). M4–M7 bauen strikt aufeinander auf — das Handbuch warnt ausdrücklich davor, KI-Einfluss auf Entries einzuführen, bevor ein separat messbarer Nutzen belegt ist (Kap. 1.1, 16.3).

---

## 8. Offener Punkt: Datenquelle

Für M1–M2 nicht nötig (synthetische Fixtures reichen). Für M3+ wird ein historischer MES Tick-/Quote-Datensatz mit Nutzungsrecht für automatisierte Auswertung benötigt (Kap. 16.1 — das Handbuch beschafft selbst keine Lizenz). Mögliche Quellen (Sierra Chart, CME Data Services, Broker-Exports) müssen auf Lizenzbedingungen für Speicherung/automatisierte Auswertung geprüft werden, bevor M3 beginnt.

---

## 9. Vorschlag für den ersten Claude-Code-Auftrag

„Implementiere M1: JSON-Schemas aus Kapitel 5 des Handbuchs (`docs/KI-Order-Flow-Ausbildung.md`) unter `schemas/`, dazu die Testfälle T01–T20 aus Kapitel 10 als pytest-Suite unter `tests/synthetic/`. Kein Netzwerk- oder Marktdatenzugriff nötig. Jeder Test referenziert seine Testnummer (T01…T20) im Testnamen und prüft exakt die im Handbuch angegebenen Erwartungswerte.“

Das lässt sich unmittelbar als erster Arbeitsauftrag in eine Claude-Code-Session geben.
