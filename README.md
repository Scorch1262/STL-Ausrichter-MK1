# STL-Ausrichter-MK1 · v1.0.0

STL-Dateien öffnen, frei im Raum drehen, die X/Y/Z-Achsen und -Ebenen gerade zur
angezeigten Lage ausrichten und die Datei **strukturgleich** wieder speichern –
auch bei STL-Dateien mit mehreren GB.

## Bedienung

| Aktion | Wie |
|---|---|
| Datei öffnen | Ins Fenster ziehen · „Öffnen …“ (Strg+O) · im Explorer „Öffnen mit“ / Datei auf die exe ziehen |
| Objekt drehen | Linke Maustaste ziehen |
| Objekt um die Blickachse drehen | Strg + linke Maustaste |
| Ansicht drehen | Rechte Maustaste |
| Ansicht verschieben | Mittlere Maustaste oder Umschalt + rechte Maustaste |
| Zoom | Mausrad |
| Genau drehen | Winkelfelder X/Y/Z oder Knöpfe −90 / −1 / +1 / +90 |
| Lage übernehmen | **ACHSEN AUSRICHTEN** (Strg+Enter) |
| Drehung verwerfen | Esc |
| Speichern | „Speichern unter …“ (Strg+Umschalt+S) |

Angezeigt werden:

* **Objekt-Ebenen XY / XZ / YZ** und **Objekt-Achsen X / Y / Z** (farbig, drehen mit dem Objekt)
* **Welt-Referenz** (graues Gitter und Rahmen, fest)

Nach „Achsen ausrichten“ liegen Objekt-Ebenen und Welt-Referenz deckungsgleich:
die soeben angezeigte Lage ist jetzt die neue Grundlage des Objekts.

**Drehpunkt**
* *Objektmitte (Lage bleibt)* – das Objekt dreht um seine Mitte, seine Position im Raum bleibt erhalten.
* *Ursprung 0/0/0* – reine Drehung um den Koordinatenursprung.

## Große Dateien / Datei bleibt unverändert

* Die Datei wird **nie komplett in den Arbeitsspeicher geladen**, sondern blockweise gelesen
  und geschrieben. Für die Darstellung wird bei sehr großen Dateien nur jedes n-te Dreieck
  angezeigt (einstellbar: „Anzeige-Detail“). **Gespeichert werden immer alle Dreiecke.**
* **Binär-STL:** 80-Byte-Header, Dreiecksanzahl, Reihenfolge der Dreiecke, das
  2-Byte-Attribut jedes Dreiecks (z. B. Farbinformationen) und evtl. angehängte Daten werden 1:1
  übernommen. Nur Eckpunkte und Normalen werden gedreht → **Dateigröße bytegenau identisch,
  keine Komprimierung, keine Vereinfachung**.
* **ASCII-STL:** Jede Zeile bleibt erhalten, Zahlen werden im selben Format (Nachkommastellen,
  Exponentenschreibweise) geschrieben; Längenunterschiede (z. B. neues Minuszeichen) werden über
  Leerzeichen ausgeglichen → Dateigröße ebenfalls identisch.
* Gerechnet wird in doppelter Genauigkeit. Drehungen um exakt 90° sind verlustfrei.
* Es wird zuerst in `<Ziel>.teil` geschrieben und erst am Ende umbenannt – bei Abbruch oder
  Fehler bleibt die Zieldatei unangetastet. Vor dem Speichern wird der freie Speicherplatz geprüft.

## Build

GitHub Actions (`.github/workflows/build-exe.yml`) baut bei jedem Push auf `main`
die Windows-exe, packt den kompletten Programmordner (exe + `_internal` mit allen
Bibliotheken) in `STL-Ausrichter-MK1-v<Version>-windows.zip` und legt ein Release an.
Die Version steht in `version.py`.

Lokal starten:

```
pip install -r requirements.txt
python main.py [datei.stl]
```
