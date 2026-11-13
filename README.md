# STL-Ausrichter-MK1 · v1.5.0

STL-Dateien öffnen, frei im Raum drehen, die X/Y/Z-Achsen und -Ebenen gerade zur
angezeigten Lage ausrichten und die Datei **strukturgleich** wieder speichern –
auch bei STL-Dateien mit mehreren GB. Optional: Umwandlung in **STEP (.stp)** und
**Vereinfachen** als STL.

![STL-Ausrichter-MK1](docs/screenshot.png)

Das Programm ist **eine einzelne exe** – ohne daneben liegende Dateien oder Ordner.

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
| Achsen an Ansicht ausrichten | **ACHSEN AUSRICHTEN** (Strg+Enter) |
| Auf Fläche legen | **FLÄCHEN ANZEIGEN** (F) → Fläche anklicken · Esc beendet |
| Ursprung in Körpermitte | „Ursprung jetzt in Körpermitte setzen“ |
| Drehung verwerfen | Esc |
| Ausgerichtet speichern (verlustfrei) | „Speichern unter …“ (Strg+Umschalt+S) |
| STEP erzeugen (optional) | „Als STEP (.stp) speichern …“ |
| Vereinfachen (optional) | „Vereinfacht als STL speichern …“ |

Angezeigt werden:

* **Objekt-Ebenen XY / XZ / YZ** und **Objekt-Achsen X / Y / Z** (farbig, drehen mit dem Objekt)
* **Welt-Referenz** (graues Gitter und Rahmen, fest)

**ACHSEN AUSRICHTEN** richtet die Achsen an der aktuellen Ansicht aus: Das Objekt bleibt auf dem
Bildschirm genau so liegen, wie es angezeigt wird. Die Achse, die am ehesten zum Betrachter zeigt,
zeigt danach **exakt zum Betrachter** (sie erscheint als Punkt); die beiden anderen verlaufen
**exakt waagerecht bzw. senkrecht** zum Fensterrand. Die Ansicht springt dazu auf die passende
Standardansicht (z. B. von vorne), ohne dass sich das Bild ändert.
Zum Feinjustieren vorher: Strg + linke Maustaste dreht das Objekt um die Blickachse.

Direkt danach werden die **Koordinaten automatisch umgesetzt**: Der Ursprung 0/0/0 wandert in die
Mitte des Körpers (Einstellung „Ursprung danach“: Mitte der Begrenzungsbox, Volumenschwerpunkt
oder nicht verschieben). Dasselbe gilt für „Auf Fläche legen“.

**Oberfläche:** Die meisten Bereiche sind im Normalzustand eingeklappt (▸) und lassen sich per
Klick auf die Überschrift öffnen (▾). Offen sind nur „Datei“ und „Ausrichten“. Welche Bereiche
offen sind, merkt sich das Programm (in der Windows-Registrierung, keine Dateien neben der exe).

**Drehpunkt**
* *Objektmitte (Lage bleibt)* – das Objekt dreht um seine Mitte, seine Position im Raum bleibt erhalten.
* *Ursprung 0/0/0* – reine Drehung um den Koordinatenursprung.

## Auf Fläche legen (wie in Bambu Studio – für alle drei Ebenen)

![Auf Fläche legen](docs/screenshot_flaeche.png)

1. **FLÄCHEN ANZEIGEN (F)** drücken. Wie bei „Auf Fläche legen“ in Bambu Studio werden alle
   ausreichend großen, ebenen Flächen des Körpers als weiße Flächen eingeblendet
   (berechnet über die konvexe Hülle; Hüllflächen mit gleicher Normale werden
   zusammengefasst, zu kleine Flächen und schmale Streifen verworfen, max. 254 Flächen).
   Die Fläche unter der Maus wird orange hervorgehoben.
2. **Zielebene** wählen: XY, XZ oder YZ – jeweils mit Richtung (z. B. „XY – Fläche nach unten (−Z)“
   entspricht dem Original: die Fläche liegt auf dem Druckbett).
3. Fläche **anklicken** – das Objekt wird so gedreht, dass die Fläche auf der gewählten Ebene liegt.
4. Danach wird der **Ursprung 0/0/0 in die Mitte des Körpers** gesetzt – wahlweise Mitte der
   Begrenzungsbox oder Volumenschwerpunkt. Berechnet wird das **exakt über alle Dreiecke der
   Datei** (nicht nur über die Anzeige).

Zusätzliche Optionen:

* **Längste Kante an Achse ausrichten** – dreht in der Ebene zusätzlich so, dass die längste
  Kante der Fläche parallel zur nächstliegenden Achse liegt (bei kantigen Teilen sind damit alle
  drei Achsen auf einen Klick gerade).
* **Folge-Ebene** – nach dem ersten Legen kann eine Seitenfläche auf eine andere Ebene gelegt
  werden (z. B. erst Boden auf XY, dann Seite auf XZ). Das Objekt dreht dann nur noch um die
  bereits festgelegte Achse, die erste Fläche bleibt liegen.
* **Ebenheit** – Toleranz in Grad, ab wann leicht unebene Bereiche (z. B. bei 3D-Scans) noch als
  eine Fläche gelten.
* Die Normale der gewählten Fläche wird über alle darauf liegenden Punkte nachgerechnet – bei
  CAD-Dateien liegt die Fläche danach exakt in der Ebene.

Auch das Legen ist verlustfrei: Es werden nur Drehung und Verschiebung gesetzt, gespeichert wird
wie gewohnt mit „Speichern unter …“.

## Ausrichten & Speichern – immer ohne Komprimierung

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

## Scan → STEP

![Scan → STEP](docs/screenshot_scan.png)

Das Programm ist auf **3D-Scans** ausgelegt. Vor der eigentlichen STEP-Umwandlung wird der Scan
automatisch aufbereitet (Bereich „Scan → STEP“):

1. **Fragmente entfernen** – Scans enthalten oft Hunderte lose Teilchen (Staub, Reflexionen,
   Tischreste). Standard: nur der größte Körper bleibt (wahlweise Teile < 5 % entfernen oder alle
   behalten). Vorher wurde jedes Teilchen als eigener Körper umgewandelt.
2. **Detailstufe** – auf eine sinnvolle Dreieckszahl vereinfachen (Standard 100.000).
3. **Löcher schließen & reparieren** – wasserdicht, ohne Selbstüberschneidungen (MeshFix).
4. **Bohrungen, Zapfen & Rundungen einpassen** – zylindrische Bereiche werden erkannt und die
   Punkte exakt auf den ausgeglichenen Zylinder gelegt: genaue Durchmesser, glatte Rundung.
5. **Ebene Flächen begradigen** – im Rahmen der Scan-Toleranz ebene Bereiche werden exakt in eine
   Ebene gelegt, Kanten zwischen zwei Ebenen exakt auf die Schnittkante. Im STEP wird daraus je
   **eine** ebene Fläche mit scharfen Kanten statt Tausender verrauschter Dreiecke.
6. **Scan-Toleranz** – automatisch aus dem Rauschen des Scans geschätzt oder fest in mm.

Danach folgt die STEP-Umwandlung (Volumenkörper, ebene Flächen zusammenfassen). Ungültige
Stellen, die bei verrauschten Daten beim Zusammenfassen entstehen können, werden automatisch
erkannt und dort als Dreiecke belassen – das Ergebnis ist immer ein geprüfter Volumenkörper.

Nach dem Speichern zeigt ein Bericht, was gemacht wurde (entfernte Fragmente, Anzahl Ebenen,
erkannte Radien, Toleranz). Mit **„Aufbereiteten Scan als STL speichern …“** lässt sich das
Ergebnis der Aufbereitung auch als STL speichern und prüfen.

Für saubere CAD-Exporte (keine Scans) kann die Aufbereitung unter „STEP-Optionen“ abgeschaltet
werden; dann werden volle Zylinder/Kugeln (bis 30.000 Dreiecke) durch echte STEP-Flächen ersetzt.

Weitere STEP-Optionen: *kompakt schreiben* (ohne P-Kurven, ca. halbe Dateigröße),
*gründliche Nachprüfung* (Datei neu einlesen – langsam), *Scan-Oberfläche glätten (NURBS)*.
Unter „Nur vereinfachen (STL)“ gibt es weiterhin die reine Vereinfachung.

Alle Aufträge laufen in einem eigenen Prozess (jederzeit abbrechbar) und verwenden die aktuelle
Ausrichtung; die Originaldatei wird nicht verändert.

## Build

GitHub Actions (`.github/workflows/build-exe.yml`) baut bei jedem Push auf `main`
eine **einzelne** `STL-Ausrichter-MK1-v<Version>.exe` (PyInstaller `--onefile`) und legt
ein Release an (exe direkt + als ZIP). Die Version steht in `version.py`.

Da alles in einer Datei steckt, entpackt die exe beim Start ihre Bibliotheken kurz in den
Temp-Ordner (wird beim Beenden automatisch entfernt). Der erste Start dauert deshalb
einige Sekunden länger.

Lokal starten:

```
pip install -r requirements.txt
python main.py [datei.stl]
```

## Versionen

* **1.5.0** – Scan-Aufbereitung für Scan → STEP: Fragmente entfernen, Detailstufe, Reparatur,
  Ebenen begradigen, Bohrungen/Rundungen einpassen, Bericht; abgesichertes Zusammenfassen
  ebener Flächen; aufbereiteten Scan als STL speichern.
* **1.4.0** – STEP-Umwandlung ca. 2–2,5× schneller; Optionen „kompakt schreiben“ und
  „gründliche Nachprüfung“.
* **1.3.0** – Bereiche einklappbar (meist eingeklappt); Ursprung wird nach „Achsen ausrichten“
  automatisch in die Körpermitte gesetzt; STEP-Fehler „No module named 'networkx'“ behoben.
* **1.2.1** – „Achsen ausrichten“ richtet die Achsen jetzt an der Ansicht aus (eine Achse exakt
  zum Betrachter, die anderen parallel zum Fensterrand); Kamera mit Drehung um die Blickachse.
* **1.2.0** – „Auf Fläche legen“ (wie Bambu Studio) für XY/XZ/YZ mit Folge-Ebene,
  Kantenausrichtung und Ursprung in Körpermitte (Box oder Volumenschwerpunkt, exakt).
* **1.1.0** – Eine einzelne exe; STEP-Umwandlung (aus STL-STEP-Konverter-MK1) und Vereinfachen
  als Option; Screenshot im README.
* **1.0.0** – Erstversion: Ansicht, Drehen, Achsen ausrichten, verlustfreies Speichern.
