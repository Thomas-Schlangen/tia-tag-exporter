# TIA Tag Exporter

Exportiert PLC-Tags, HMI-Tags und DB-Variablen aus einem TIA-Portal-Projekt
über die **TIA Portal Openness API** in eine strukturierte Excel-Datei.

## Voraussetzungen

- **TIA Portal V21** (bzw. eine Version, deren Openness-DLL das Zielprojekt
  öffnen kann — siehe [`docs/setup-notes.md`](docs/setup-notes.md))
- **Python 3.11+**
- **Windows** (die Openness API ist eine Windows-.NET-Assembly)

## Installation

```bash
pip install -e .
```

## Konfiguration

1. `config.example.yaml` nach `config.yaml` kopieren.
2. Pfade anpassen:

```yaml
tia:
  version: "V21"  # Vorauswahl im GUI-Dropdown
  versions:
    V19:
      dll_path: "C:/Program Files/Siemens/Automation/Portal V19/PublicAPI/V19/Siemens.Engineering.dll"
    V20:
      dll_path: "C:/Program Files/Siemens/Automation/Portal V20/PublicAPI/V20/Siemens.Engineering.dll"
    V21:
      dll_path: "C:/Program Files/Siemens/Automation/Portal V21/PublicAPI/V21/net48/Siemens.Engineering.Base.dll"
export:
  output_dir: "output"
  include_plc_tags: true
  include_hmi_tags: true
  include_db_variables: true
logging:
  level: "INFO"
  file: "export.log"
  console: true
```

`config.yaml` wird von `.gitignore` ausgeschlossen, da Projektpfade
Environment-spezifisch sind. Die Datei wird per
[`config_loader`](src/config_loader) gegen ein Pydantic-v2-Schema
(`src/tia_tag_exporter/config_schema.py`) validiert.

**Zwei DLL-Layouts, automatisch erkannt:** Ab V21 ist die Openness API in
mehrere Assemblies aufgeteilt (`dll_path` zeigt auf
`Siemens.Engineering.Base.dll`); vor V21 (V19/V20) ist es eine einzige
`Siemens.Engineering.dll` ohne `net48`-Unterordner, die dann von
`Siemens.Engineering.Contract.dll` unter `<Installationswurzel>\Bin\PublicAPI`
abhängt (nicht im `PublicAPI`-Baum selbst) — `TiaConnector` bindet diesen Pfad
automatisch mit ein. `TiaConnector` erkennt anhand des Dateinamens von
`dll_path`, welches Layout vorliegt, und lädt die passenden Begleit-Assemblies
aus demselben Ordner. Sowohl WinCC Advanced/Comfort als auch WinCC Unified
werden bei allen drei Versionen erfasst.

## Verwendung

```bash
tia-tag-exporter
```

Startet ein GUI-Fenster (Tkinter) — es gibt keinen CLI-Modus mehr. Im Fenster:

1. TIA-Version wählen (Dropdown, vorausgewählt aus `config.yaml`).
2. TIA-Projekt auswählen (`.ap19`/`.ap20`/`.ap21`).
3. Ausgabeordner wählen (vorausgewählt aus `config.yaml`).
4. Gewünschte Kategorien (PLC-Tags, HMI-Tags, DB-Variablen) ankreuzen.
5. "Start Export" klicken — der Export läuft in einem Hintergrund-Thread,
   die Statuszeile unten zeigt Fortschritt und Fehler an.

**Hinweis:** TIA Portal muss zum Export nicht geöffnet sein — der Zugriff
erfolgt headless über `TiaPortalMode.WithoutUserInterface`.

## Ausgabe

Eine Excel-Datei mit einem Deckblatt und bis zu drei weiteren Arbeitsblättern:

| Sheet | Spalten |
|---|---|
| Deckblatt | Kunde, Projekt, Anlage, Erstellt von, Datum, Version, Bemerkung (zum Ausfüllen) |
| PLC-Tags | Variablentabelle, Name, Datentyp, Adresse, Kommentar, Zugriffsebene |
| HMI-Tags | Variablentabelle, Name, Datentyp, Verbindung, PLC-Variable, Kommentar, Quellkommentar |
| DB-Variablen | DB-Name, Pfad, Ordnerebene 1..N, Variablenname, Datentyp, Offset, Kommentar, Initialwert |

Im DB-Variablen-Sheet ist Spalte A ("DB-Name") die Gruppierungsspalte; Spalte
B ("Pfad") enthält den vollständigen Ordnerpfad des Datenbausteins als Text,
die Ebenen mit " - " verbunden (z. B. `PLC_1 - Programmbausteine - 01 [4805]
DrySaltingMachine - DDb`); danach bildet je eine weitere Spalte dieselbe
Ordnerebene einzeln ab (von der PLC-Wurzel bis zum direkten Elternordner —
der DB selbst ist weder in "Pfad" noch in den Ordnerebene-Spalten enthalten).
Zeilen desselben DBs sind per Gliederung (`outline_level`) gruppiert und
lassen sich links über +/- ein- und ausklappen; zwischen den DB-Blöcken steht
je eine Leerzeile. Kopfzeile ist fett formatiert, die erste Zeile
eingefroren, Spaltenbreiten werden automatisch an den Inhalt angepasst.

Dasselbe Gruppieren-und-Einklappen gilt auch für PLC-Tags und HMI-Tags: dort
werden die Zeilen nach Spalte A ("Variablentabelle") gruppiert, mit
Leerzeile zwischen den Tabellen-Blöcken.

## Logging

Alle Läufe werden über [`my_logger`](src/my_logger) (stdlib `logging`)
protokolliert, konfiguriert über `config.yaml` (`[logging]`):
- Konsole: Level aus `logging.level`
- Datei `export.log` (im Arbeitsverzeichnis): dasselbe Level

Fehler beim Lesen einzelner Tags brechen den Export nicht ab — sie werden als
Warnung geloggt, der Export läuft mit den übrigen Tags weiter.

## Projektstruktur

```
tia-tag-exporter/
├── src/
│   ├── tia_tag_exporter/
│   │   ├── main.py          # Einstiegspunkt: Config laden, Logger init, GUI starten
│   │   ├── gui.py           # Tkinter-Oberfläche
│   │   ├── config_schema.py # Pydantic-v2-Schema für config.yaml
│   │   ├── connector.py     # TIA Openness Verbindung (pythonnet)
│   │   ├── extractor.py     # Tag-Extraktion (PLC/HMI/DB)
│   │   └── exporter.py      # Excel-Export (openpyxl)
│   ├── config_loader/       # Wiederverwendbare YAML/JSON-Config-Bibliothek
│   └── my_logger/           # Wiederverwendbare Logging-Bibliothek (stdlib logging)
├── tests/
├── docs/
│   └── setup-notes.md   # DLL-Suchergebnis & Versionshinweise
└── config.example.yaml
```

## Bekannte Einschränkungen

Live gegen ein reales V21-Projekt getestet (435 PLC-Tags, ~104.000
DB-Variablen, 1085 HMI-Tags erfolgreich exportiert). Dabei bestätigt:

- **DB-Variablen: `Offset` bleibt bei optimierten Datenbausteinen (Optimized
  Block Access) leer.** Das ist keine Extraktionslücke, sondern eine harte
  Grenze der Openness API: Optimierte DBs (in modernen TIA-Projekten der
  Regelfall) haben keinen festen Byte-Offset. Nur bei "Standard"-Zugriff
  (nicht optimiert) ist dieser Wert überhaupt vorhanden.
- **DB-Variablen: `Kommentar` kommt nicht aus `Interface.Member` selbst.**
  `Siemens.Engineering.SW.Blocks.Interface.Member` hat live erschöpfend
  verifiziert (alle Member-Typen eines realen Projekts) kein Comment-Attribut
  — weder einfach noch mehrsprachig (`CommentML`). Kommentare für
  DB-Variablen liegen stattdessen ausschließlich in TIA Portals zentraler
  Projekttexte-Verwaltung ("Sprachen & Ressourcen > Projekttexte"). Der
  Export liest diese daher zusätzlich über
  [`project_texts.py`](src/tia_tag_exporter/project_texts.py) aus
  (`Project.ExportProjectTexts()`, Kategorie `<BlockCommentCategoryData>`)
  und ordnet sie über PLC-Name + DB-Name + Membername den DB-Variablen zu
  (nicht über eine direkte Objekt-Referenz, sondern per Pfad-String aus dem
  Export — der PLC-Name macht den Schlüssel projektweit eindeutig, siehe
  Docstring der Klasse).
- **HMI-Tags bei WinCC Advanced/Comfort: `Datentyp`, `Verbindung` und
  `PLC-Variable` kommen nicht aus dem Tag-Objekt selbst.** Weder
  `Siemens.Engineering.Hmi.Tag.Tag` noch die generische
  `GetAttribute`-Schnittstelle kennen dafür ein Attribut (live erschöpfend
  verifiziert, `GetAttributeInfos()` liefert nur `["Name"]`). Alle drei
  stecken aber im XML von `HmiTagTable.Export()`: jedes Tag-Element hat eine
  `<LinkList>` mit u. a. `<DataType>`, `<Connection>` und `<ControllerTag>`
  (jeweils `<Name>`). `DataType` ist dort bei jedem Tag vorhanden (auch rein
  internen), `Connection`/`ControllerTag` fehlen komplett bei internen, nicht
  mit der PLC verknüpften Tags. Der Export läuft einmal pro Tag-Tabelle
  (nicht pro Tag) und wird intern geparst. Bei WinCC Unified sind
  `DataType`/`Connection` echte Properties und werden bevorzugt direkt
  gelesen, ohne diesen Umweg.
- **HMI-Tags: `Kommentar` kommt bei WinCC Advanced/Comfort ebenfalls aus den
  Projekttexten, nicht aus dem Tag-Objekt.** Der eigene Kommentar eines
  Advanced/Comfort-Tags ist über Openness nicht abrufbar, existiert aber in
  der Projekttexte-Kategorie `<HMI comment>` mit einem `ViewPath` wie
  `{Projekt}\{HMI-Gerät}\HMI-Variablen\{Tag-Tabelle}\{Tag-Name}\Kommentar`.
  Wichtig: Der Gerätename im Pfad ist **nicht** `hmi.Name` (das ist der Name
  des Software-Containers, z. B. `HMI_RT_8`), sondern der Name des
  Hardware-Geräts (z. B. `pn4805-15A10`) — wird über die `Parent`-Kette
  ermittelt. Bei WinCC Unified ist `Comment` eine echte Property und wird
  direkt gelesen, ohne diesen Umweg.
- **HMI-Tags: `Quellkommentar` ist NICHT der Kommentar des HMI-Tags
  selbst, sondern der Kommentar der verknüpften PLC-Variable** (die
  "Quelle" der HMI-Variable) — eine eigenständige Spalte, kein Ersatzwert
  für `Kommentar`. Bei WinCC Advanced/Comfort ist der eigene Tag-Kommentar
  über Openness ohnehin nicht abrufbar, daher bleibt `Kommentar` dort meist
  leer, während `Quellkommentar` über `PLC-Variable` (DB-Name + Membername)
  in denselben Projekttexten nachschlägt, die auch für die
  DB-Variablen-Kommentare genutzt werden. Ohne PLC-Namen im Schlüssel (an
  dieser Stelle nicht bekannt, zu welcher PLC die Ziel-DB gehört) — bei
  mehreren PLCs mit gleichnamigem DB und Member theoretisch mehrdeutig,
  in der Praxis vernachlässigbar. Live verifiziert: 65 von 177 HMI-Tags
  in einem realen Projekt erhalten so einen `Quellkommentar`.
- **WinCC Unified** ist bisher nur anhand der .NET-Typsignaturen verifiziert
  (per Reflection: `HmiUnified.HmiTags.HmiTag` hat echte `DataType`-,
  `Connection`- und `Comment`-Properties), aber noch nicht live gegen ein
  Projekt mit tatsächlichem Unified-Gerät getestet.
- Die Openness-DLL ist versionsgebunden — ein mit V21 angelegtes Projekt lässt
  sich nicht ohne Weiteres mit einer V19-DLL öffnen (siehe `docs/setup-notes.md`).
- **TIA Portal V19 (headless) ist bei der DB-Variablen-Extraktion instabil.**
  Live an zwei unabhängigen echten V19-Projekten reproduziert: Die
  Openness-Session (`TiaPortalMode.WithoutUserInterface`) kann nicht-
  deterministisch mitten in der Extraktion sterben
  (`EngineeringObjectDisposedException`, "TIA Portal has either been disposed
  or stopped running") — mal nach wenigen Sekunden, mal gar nicht, ohne
  erkennbare Korrelation zu Projektgröße oder DB-Struktur. `run_export()`
  fängt das ab und verbindet automatisch neu (bis zu `_MAX_RECONNECT_ATTEMPTS`,
  Fortschritt wird pro PLC/DB/HMI-Target gemerkt), aber bei größeren Projekten
  (>100 DBs) kann das Instabilitätsmuster so häufig auftreten, dass selbst das
  nicht zuverlässig durchläuft. PLC-Tags und HMI-Tags sind davon nicht
  betroffen (deutlich weniger Openness-Calls) und laufen auf V19 zuverlässig.
  Bei V21 bislang nicht beobachtet (49.453 DB-Variablen live erfolgreich
  exportiert, siehe oben). Wirkt wie ein Engine-seitiges Verhalten der
  Siemens-Openness-Implementierung in V19, nicht wie ein Bug in diesem Tool.

Details und die konkreten `GetAttributeInfos()`-Ergebnisse siehe
`docs/setup-notes.md`.
