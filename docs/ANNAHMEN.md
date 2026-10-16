# Implementierungsannahmen und Auslegungen

Das Handbuch (`docs/KI-Order-Flow-Ausbildung.md`) legt Regeln und Startwerte fest, lässt aber einige
technische Details offen. Diese Datei hält fest, wie die Implementierung sie auslegt. Jede Annahme ist
Teil der eingefrorenen Versionen `OF-A-1.0` (Policy), `SIM-1.0` (Simulation) bzw. `FEAT-1.0` (Merkmale).
Eine Änderung braucht eine neue Version und die betroffenen Regressionstests (Kap. 13.5).

## Zeitmodell und Replay

| # | Thema | Auslegung | Handbuch |
|---|---|---|---|
| Z1 | Uhr des Systems | Entscheidungen laufen auf der Empfangszeit (`receive_ts`). Ereignisse werden strikt in Empfangsreihenfolge freigegeben (Empfangszeit, dann Stream, dann Sequenz). | 5.2, 6.1 |
| Z2 | Balkenverfügbarkeit | Balken werden in Börsenzeit `[Beginn, Ende)` gebildet und sind ab `Ende + 100 ms` Empfangszeit verfügbar (`SIM-1.0: bar_finalization_ms`). Ein danach empfangenes Ereignis ändert den versiegelten Balken nicht und wird als `LATE_EVENT` protokolliert. | 6.1 |
| Z3 | Fehlende Empfangszeit | `receive_ts = exchange_ts + 20 ms` (`assumed_receive_latency_ms`), Ereignis markiert. Dann gilt nur ein Börsenzeit-Replay mit angenommener Verzögerung. | 4.2, 6.1 |
| Z4 | Gleichstand | Bei gleichem Zeitstempel: Marktereignisse vor Balkenabschluss vor Order-/Regeltimern. Eine gleichzeitig eingetroffene Marktinformation gewinnt also immer (konservativ). | 7.4 |
| Z5 | Snapshot-Inhalt | Statt einer Liste aller Ereignis-Hashes speichert der Snapshot `event_count` und eine SHA-256-Kette (`events_digest`) über alle bis dahin verarbeiteten Ereignisse. Der Prüfer (`verifier/timing.py`) rechnet sie unabhängig nach. | 5.2 |
| Z6 | Sequenzen | Je `stream_id` geprüft. Lücke → `DATA_GAP`; widersprüchliches Duplikat oder ältere Sequenz → wie Datenlücke behandelt; identisches Duplikat → verworfen. | 4.2 |

## Regelmodul OF-A-1.0

| # | Thema | Auslegung | Handbuch |
|---|---|---|---|
| R1 | Signalzeit | Der Ausbruchsbalken muss vollständig in `[10:00, 15:30)` New York liegen (Beginn ≥ 10:00, Ende ≤ 15:30). | 7.1 |
| R2 | Warm-up | Vor dem Ausbruchsbalken müssen 20 vollständige Balken lückenlos aufeinander folgen. Nach Datenlücke, Handelsunterbrechung oder Neustart beginnt die Zählung neu. Das Flag `HALT_NEW_ENTRIES` wegen `DATA_GAP` fällt erst nach diesem Warm-up weg. | 4.2, 7.1, 7.2 |
| R3 | Vorheriger Schluss | „Letzter Schlusskurs vor diesem Balken“ = Schluss des letzten vorherigen Balkens mit mindestens einem Trade. | 7.2 |
| R4 | Wartezeit | Gezählt werden 5 vollständige Balken, die bei oder nach der Börsenzeit des verwerfenden Ereignisses beginnen. Ein angebrochener Balken (z. B. Barrieretreffer mitten im Balken) zählt nicht. | 7.3 |
| R5 | Verbrauch eines Kandidaten | Verfall der Signalgültigkeit, `NO_TRADE_RISK`, Stoppabstand außerhalb 4–24 Ticks, Filter-Ablehnung und Nachrichten-/Datenproblem verwerfen den Kandidaten ebenfalls (Wartezeit, bzw. `DONE_FOR_SESSION` nach dem dritten Kandidaten). | 7.1, 7.3 |
| R6 | Geplanter Fill | Für Ordervorschlag und Positionsgröße zählt der zuletzt empfangene Bid (abzüglich Zusatz-Slippage). Ohne Quote wird der Bestätigungsschluss verwendet. Das Ausführungsmodul prüft später am dann verfügbaren Markt erneut. | 7.1, 7.4 |
| R7 | Ausstiegsauslöser | Stopp und Ziel werden nur von Trades ausgelöst, deren Börsenzeit bei oder nach dem Entry-Fill liegt. | 7.4 |
| R8 | Entry-Schluss | Ein Entry-Fill ist nur vor 15:55 New York zulässig (danach greift ohnehin der Zeitausstieg). Praktisch bindet die Signalzeit früher. | 7.4 |
| R9 | Nachrichtensperre | Ein angekündigter Termin zählt nur, wenn er zum Entscheidungszeitpunkt bekannt war (`known_since`). Gesperrt ist jeder Balken, dessen Intervall das Fenster ±5 Minuten berührt. Fehlt der Nachrichtenkalender (`news = None`), ist die Sitzung nicht prüfbar. | 7.1 |
| R10 | Entscheidungstakt | Bei jedem versiegelten Balken im Referenzfenster entsteht eine protokollierte Entscheidung, auch bei `NO_TRADE` (vollständiges Journal inkl. Nicht-Handeln). Dazu kommen ereignisgetriebene Entscheidungen (Verwerfung, Ablauf, Sperre). | 1.2, 16.3 |

## Ausführung und Kosten (SIM-1.0)

| # | Thema | Auslegung | Handbuch |
|---|---|---|---|
| A1 | Entry | Die Order wird zum Entscheidungszeitpunkt gesendet und trifft nach 250 ms ein. Erst dann prüft das Ausführungsmodul Signalalter (≤ 2 s), Quote-Alter (≤ 250 ms seit Empfang des Quotes), Spread (≤ 2 Ticks), sichtbare Bid-Menge, Stoppabstand (4–24 Ticks) und Geldrisiko. Bei Erfolg Fill zum Bid. | 7.1, 7.4, 8.2 |
| A2 | Erneuter Versuch | Scheitert die Prüfung, wartet die Order auf den nächsten Quote und wird dann erneut gesendet (wieder 250 ms), nur solange die Signalgültigkeit reicht. Danach `NO_TRADE_EXPIRED`. | 7.4, T13 |
| A3 | Cancel | Ein Cancel braucht dieselbe Verzögerung. Eine bereits gesendete Order kann deshalb trotz Cancel gefüllt werden; der Fill bleibt im Journal und wird als offene Position verwaltet. | 7.4 |
| A4 | Exit | Marktrückkauf zum sichtbaren Ask nach 250 ms Exit-Verzögerung. Ohne sichtbare Ask-Menge wird auf den nächsten Quote gewartet (`delayed_for_liquidity`). Für Exits gibt es kein Quote-Alter-Gate; das Alter wird gespeichert. | 7.4, 8.2 |
| A5 | Risikoüberschreitung | Liegt nach dem Fill der Abstand außerhalb 4–24 Ticks oder reicht das Budget nicht, folgt sofort ein Exit `RISK_OVERRUN`. Im aktuellen Modell (Prüfung zum Fill-Zeitpunkt) tritt das praktisch nicht ein; der Pfad ist für spätere Modelle vorhanden. | 8.1 |
| A6 | Kostenansatz | Nur Ansatz 1 (realistische Fill-Preise + explizite Gebühren). Die 2-Tick-Reserve dient nur der Positionsgröße und wird nicht als Kosten abgezogen. | 8.2, 12.3 |
| A7 | Limit-Orders | Nicht Teil von OF-A-1.0. Nur das explizite FIFO-Testmodell für T08 existiert. | 8.2 |

## Datenstörungen und Betrieb

| # | Thema | Auslegung | Handbuch |
|---|---|---|---|
| D1 | Lücke bei offener Position | Der gesamte Trade wird `UNSCORABLE` und separat ausgewiesen; Stopp, Ziel und Zeitausstieg bleiben aktiv. | 4.2, T18 |
| D2 | Handelsunterbrechung | `STATUS HALT/CLOSED` sperrt neue Einstiege; Balken, die die Unterbrechung berühren, sind unvollständig. Eine offene Position wird dadurch nicht `UNSCORABLE`. | 4.2, 7.1, 7.4 |
| D3 | Korrekturen/Busts | Betrifft die Meldung einen noch offenen Balken, wird er unvollständig. Betrifft sie einen versiegelten Balken, wird sie als spätere Datenversion protokolliert; alte Snapshots bleiben unverändert. | 5.1, 6.1 |
| D4 | Neustart | Maßgeblich sind die bestätigten Fills im Journal. Ausstehende, nicht gefüllte Entry-Orders werden storniert (`RESTART_RECOVERY`), ein bewaffneter Kandidat verworfen. Ein ausstehender Exit wird fortgesetzt. | 5.4, T17 |
| D5 | Kalender | Es wird kein offizieller Börsenkalender mitgeliefert. Der Testkalender in `tests/fixtures` ist synthetisch. Für reale Daten (M3) muss ein geprüfter Kalender hinzukommen. | 4.1, T19 |

## Prüfer

| # | Thema | Auslegung | Handbuch |
|---|---|---|---|
| P1 | Begriffsprüfung | `verifier/claims.py` ist eine feste Regelliste (Delta als Nettokauf, Manipulation, Identität, garantierter Fill, unkalibrierte Prozentwerte). Sätze mit Negation gelten als zulässige Abgrenzung. Keine semantische Bewertung. | 3, 9 Stufe A |
| P2 | Fill-Prüfung | Jeder Fill muss zum zuletzt empfangenen Quote passen (Preis, sichtbare Menge, Verzögerung). | 8.2, 14.1 |
| P3 | Experimentregister | Ein Zeitraum mit mehr als einer Sichtung oder einer anderen Rolle als `HOLDOUT` liefert keinen unabhängigen Nachweis. Er wird als `CONSUMED_AS_VALIDATION` markiert; neue Holdouts müssen nach allen bisher genutzten Zeiträumen liegen. | 12.1, 14.3, T20 |
