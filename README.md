# Order-Flow-Agent (OF-A-1.0)

Python-Umsetzung des Ausbildungs- und Prüfhandbuchs
[`docs/KI-Order-Flow-Ausbildung.md`](docs/KI-Order-Flow-Ausbildung.md) (Version 1.0, 23.09.2026) nach dem
Bauplan [`PLAN.md`](PLAN.md).

**Betriebsart:** nur historisches Replay (`REPLAY`) bzw. Beobachtung (`SHADOW`), **keine echten Orders**.
Es gibt keinen Broker-Anschluss. Bestandene Tests belegen technische Regelkonformität, nicht Profitabilität.

## Stand der Meilensteine

| Meilenstein | Status | Nachweis |
|---|---|---|
| **M1** Schemas & synthetische Selbsttests | erledigt | `schemas/`, `tests/synthetic/`, PASS/FAIL-Bericht `reports/m1/selftest_results.json` |
| **M2** Deterministisches Regelmodul & Fill-Simulator | erledigt (synthetisch) | `src/orderflow/rules`, `src/orderflow/execution`, Bericht `reports/m2/synthetic/summary.json` |
| **M3** Datenadapter für reale MES-Daten | blockiert: Datenquelle/Lizenz offen (PLAN.md Abschnitt 8) | allgemeine Prüfungen aus Kap. 4.2 sind implementiert; Vendor-Parser, Börsenkalender und Datenqualitätsbericht fehlen |
| **M4** KI-Analyst als Diagnosewerkzeug | offen | nur der Vertrag existiert (`src/orderflow/analyst/interface.py`) |
| **M5** KI-Filter als eigener Kandidat | offen | Zeitmodell für verspätete Antworten (T14) ist im Regelmodul vorhanden |
| **M6** Backtest ohne Datenleck | offen | Experimentregister (T20) vorhanden |
| **M7** Holdout & Shadow-Modus | offen | – |

## Schnellstart

```bash
pip install -e ".[dev]"
pytest                                   # alle Tests (~1 min)
orderflow selftest                       # T01–T20 + Invarianten -> reports/m1/selftest_results.json
orderflow replay-synthetic --seeds 1-20  # vollständige Sitzungen + Prüfung -> reports/m2/synthetic/summary.json
```

## Aufbau

Die sieben Komponenten aus Kap. 2 sind getrennte Pakete. Welche Pakete sich gegenseitig importieren dürfen,
ist in `tests/invariants/test_boundaries.py` festgelegt und wird bei jedem Testlauf geprüft. Der KI-Analyst kann
damit weder Fills noch Prüfergebnisse erzeugen, und außer der Betreiber-CLI importiert niemand den Prüfer.

| Paket | Komponente (Kap. 2) | Inhalt |
|---|---|---|
| `dataadapter` | Datenadapter | Schema-Validierung und Normalisierung (Kap. 5.1), Sequenz-/Duplikatprüfung je Stream (Kap. 4.2), Wissensstand-Digest |
| `features` | Merkmalsberechnung | V, D, K, DR, VWAP, Spread, Imbalance mit `ZERO_DENOMINATOR` (Kap. 6), Balken `[Beginn, Ende)`, Sitzungs-/Nachrichtenkalender |
| `rules` | Regelmodul | Zustandsautomat `IDLE … DONE_FOR_SESSION` + `HALT_NEW_ENTRIES` (Kap. 7.2–7.5) |
| `analyst` | KI-Analyst | Vertrag: schreibgeschützte Sicht, Lernprotokolle nach Kap. 13.3 |
| `execution` | Risiko & Ausführung | Positionsgröße, idempotente Orders, Fill-Simulator ohne fiktive Fills (Kap. 8) |
| `verifier` | Externer Prüfer | unabhängige Fill- und Zeitprüfung, Invarianten, Begriffsprüfung, Experimentregister |
| `journal` | Versionsverwaltung | append-only Journal mit Hash-Kette (Kap. 5.4) |
| `replay` | (Replay-Controller, Kap. 16.2) | gibt Ereignisse in Empfangsreihenfolge frei; synthetische Sitzungen |
| `common` | – | Einheiten (Ticks, Decimal), Zeit, Hashes, Schemas, eingefrorene Konfiguration |

Konfiguration (eingefroren, versioniert, nie im Code):

- `config/policies/OF-A-1.0.toml`: alle Strategieparameter aus Kap. 7.1
- `config/simulation/SIM-1.0.toml`: Kosten, Latenzen, Stressszenarien (Kap. 8)
- `config/contracts/MES.toml`: Kontraktspezifikation (Kap. 4)

Auslegungen, wo das Handbuch Details offenlässt: [`docs/ANNAHMEN.md`](docs/ANNAHMEN.md).

## Tests

| Ordner | Inhalt |
|---|---|
| `tests/synthetic/` | T01–T20 aus Kap. 10 und der Lernfall aus Kap. 11. Jeder Test trägt seine Testnummer und prüft die im Handbuch genannten Sollwerte. T09–T18 laufen über den vollständigen Replay-Pfad. |
| `tests/invariants/` | Invarianten Kap. 7.5 bei jeder Zustandsänderung, automatische Eigenschaften Kap. 10.4 auf Zufallssitzungen, Gegenproben für den Prüfer, Paketgrenzen |
| `tests/unit/` | Schemas (Handbuchbeispiele gültig, Verstöße abgewiesen), Journal, Analyst-Vertrag, Schnellprüfung ≡ Schema |
