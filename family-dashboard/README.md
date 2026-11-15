# Haushaltsplaner mit Sparschwein (Family Dashboard)

Wand-Dashboard für den Haushalt: Aufgaben je Kind abhaken, Sparschwein füllen, Taschengeld auszahlen.
Umsetzung des Grobkonzepts v3 als **eine einzige Datei** [`index.html`](index.html): HTML, CSS und JavaScript inline,
ohne Build-Schritt, ohne Frameworks, ohne CDN, komplett offline.

Zielgerät: Amazon Fire HD 8 im Querformat mit Fully Kiosk Browser.

## Installation auf dem Tablet

1. `index.html` auf das Gerät kopieren, z. B. nach `/sdcard/Download/haushaltsplaner/index.html`.
2. Im Fully Kiosk Browser als Start-URL `file:///sdcard/Download/haushaltsplaner/index.html` eintragen.
3. Empfohlene Fully-Einstellungen:
   - *Web Content Settings → Enable File Uploads* an (für den Backup-Import per Datei)
   - *Web Content Settings → Enable Downloads* an (für den Export per Datei)
   - *Device Management → Keep Screen On* nach Bedarf
4. Beim ersten Start Eltern-PIN festlegen und Profile anlegen (optional mit Beispielaufgaben).
5. **Gleich danach testen, ob die Daten einen Neustart überleben** (Konzept Abschnitt 2):
   eine Aufgabe abhaken, Fully komplett beenden, Gerät neu starten, Haken prüfen.
   Unter ⚙️ → *Speicher* zeigt die App, ob `localStorage` und die IndexedDB-Zweitablage funktionieren.

## Funktionsumfang

| Konzept | Umsetzung |
|---|---|
| 3 Profile, Aufgaben, Zuordnungen | getrennte Listen im Datenmodell (`profiles`, `tasks`, `assignments`, `completions`) |
| 3.1 Zeitpläne | feste Wochentage (Mo–So) oder „alle N Tage ab Startdatum“; Tagesdifferenz in UTC-Mitternacht, daher sommerzeitfest; Wochentag wird neben dem Startdatum angezeigt |
| 3.2 Wert | Standardwert je Aufgabe, optional abweichender Wert je Zuordnung; beim Abhaken als Momentaufnahme gespeichert |
| 3.3 Symbol | Emoji-Palette plus Freitext; ohne Symbol runder Kreis mit zwei Initialen und stabiler Farbe aus dem Namen |
| 4.1 Tabs & Pinnen | gepinnte Profile werden gleichzeitig nebeneinander angezeigt, Breite gleichmäßig geteilt |
| 4.2 Wochenraster | Mo–So, Heute hervorgehoben, Navigation ◀ ▶ Heute, Kalenderwoche |
| 4.3 Swimlanes | eine Aufgabenspalte, gemeinsame Aufgaben zuerst und nur einmal; profilspezifische Zeilen sind bei anderen Profilen schraffiert und nicht bedienbar |
| 4.4 Zellzustände | nicht fällig / offen / erledigt / gesperrt (Zukunft gestrichelt, Vergangenheit mit Schloss); erledigte Zellen bleiben auch nach Zeitplanänderung sichtbar |
| 4.5 Sparschwein | SVG, Münze fällt in den Schlitz, Schwein wackelt, Betrag zählt hoch, Füllhöhe wächst |
| 4.6 Auszahlung | PIN-geschützt, Teilbetrag möglich, Historie mit Datum und Betrag |
| 4.7 PIN | Nummernfeld, gespeichert nur als gesalzener SHA-256-Hash, Entsperrung 2 Minuten (verlängert sich bei Aktivität in geschützten Dialogen), Sperre nach 5 Fehlversuchen für 30 s |
| 4.8 Aufgabenmaske | öffnet im Lesemodus; „Bearbeiten“ fragt den PIN ab; fällt nach Ablauf der Entsperrung in den Lesemodus zurück |
| 5 Verwaltung | Profile (Name, Farbe, Avatar, Reihenfolge, Löschen), Aufgabenreihenfolge, PIN ändern, Füllhöhe des Sparschweins, Backup |
| Backup | Export als JSON-Datei oder als Text zum Kopieren; Import per Datei oder eingefügtem Text |
| Speicherung | `localStorage` (Schlüssel `haushaltsplaner.v2`), zusätzlich gespiegelt in IndexedDB; beim Start gewinnt der neuere Stand. Warnbanner, wenn beides nicht funktioniert |

## Annahmen (aus Konzept Abschnitt 8 übernommen bzw. ergänzt)

- Zukünftige Tage sind nicht abhakbar; ein gemeinsamer Eltern-PIN für alle Profile.
- Die Aufgabenmaske ist ohne PIN im Lesemodus einsehbar.
- Tippen auf einen nicht gepinnten Tab, während andere Tabs gepinnt sind, zeigt einen Hinweis; die Ansicht folgt den gepinnten Profilen.
- Aufgaben ohne jede Zuordnung erscheinen am Ende der Liste, damit sie über die Maske erreichbar bleiben.
- Profil löschen entfernt auch dessen Erledigungen und Auszahlungen (mit Rückfrage).
- Datumsangaben für Auszahlungen werden als lokale Zeit `YYYY-MM-DDTHH:MM:SS` gespeichert.

## Technische Hinweise

- Bewusst konservatives JavaScript (ES5/ES6 ohne `?.`, `??`, Flexbox-`gap`) für ältere Android-WebViews.
- `crypto.subtle` steht unter `file://` oft nicht zur Verfügung, deshalb eine eingebaute SHA-256-Implementierung.
- Zum lokalen Testen am Rechner genügt es, `index.html` im Browser zu öffnen.
