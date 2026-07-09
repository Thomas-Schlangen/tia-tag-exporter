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

1. `config.example.toml` nach `config.toml` kopieren.
2. Pfade anpassen:

```toml
[tia]
project_path = "C:/Users/thomas/Documents/TIA-Projekte/MeinProjekt.ap21"
dll_path = "C:/Program Files/Siemens/Automation/Portal V21/PublicAPI/V21/Siemens.Engineering.dll"

[export]
output_path = "C:/Users/thomas/Desktop/tag-export.xlsx"
include_plc_tags = true
include_hmi_tags = true
include_db_variables = true
```

`config.toml` wird von `.gitignore` ausgeschlossen, da Projektpfade
Environment-spezifisch sind.

## Verwendung

```bash
# Nutzt config.toml im aktuellen Verzeichnis
tia-tag-exporter --plc --hmi --db

# Explizite Pfade, unabhängig von config.toml
tia-tag-exporter --project "D:/Projekte/Anlage1.ap21" --output "D:/Export/tags.xlsx" --plc --db

# Andere Konfigurationsdatei verwenden
tia-tag-exporter --config "D:/Konfigurationen/anlage1.toml" --plc
```

Ohne `--plc`/`--hmi`/`--db` wird auf die Flags aus `config.toml`
(`[export] include_*`) zurückgegriffen. Wird keines davon aktiviert, bricht
das Tool mit einer Fehlermeldung ab, statt eine leere Datei zu erzeugen.

**Hinweis:** TIA Portal muss zum Export nicht geöffnet sein — der Zugriff
erfolgt headless über `TiaPortalMode.WithoutUserInterface`.

## Ausgabe

Eine Excel-Datei mit bis zu drei Arbeitsblättern:

| Sheet | Spalten |
|---|---|
| PLC-Tags | Name, Datentyp, Adresse, Kommentar, Zugriffsebene |
| HMI-Tags | Name, Datentyp, Verbindung, Kommentar |
| DB-Variablen | Name, Datentyp, Offset, Kommentar, Initialwert |

Kopfzeile ist fett formatiert, die erste Zeile eingefroren, Spaltenbreiten
werden automatisch an den Inhalt angepasst.

## Logging

Alle Läufe werden über [`loguru`](https://github.com/Delgan/loguru) protokolliert:
- Konsole: `INFO` und höher
- Datei `export.log` (im Arbeitsverzeichnis, rotiert bei 1 MB): `DEBUG` und höher

Fehler beim Lesen einzelner Tags brechen den Export nicht ab — sie werden als
Warnung geloggt, der Export läuft mit den übrigen Tags weiter.

## Projektstruktur

```
tia-tag-exporter/
├── src/tia_tag_exporter/
│   ├── main.py          # CLI-Einstiegspunkt
│   ├── connector.py     # TIA Openness Verbindung (pythonnet)
│   ├── extractor.py     # Tag-Extraktion (PLC/HMI/DB)
│   └── exporter.py      # Excel-Export (openpyxl)
├── tests/
├── docs/
│   └── setup-notes.md   # DLL-Suchergebnis & Versionshinweise
└── config.example.toml
```

## Bekannte Einschränkungen

Live gegen ein reales V21-Projekt getestet (435 PLC-Tags, ~104.000
DB-Variablen, 1085 HMI-Tags erfolgreich exportiert). Dabei bestätigt:

- **DB-Variablen: `Offset` und `Kommentar` bleiben bei optimierten
  Datenbausteinen (Optimized Block Access) leer.** Das ist keine
  Extraktionslücke, sondern eine harte Grenze der Openness API: Optimierte
  DBs (in modernen TIA-Projekten der Regelfall) haben keinen festen
  Byte-Offset, und einzelne Interface-Member exponieren dort keinen
  Kommentar. Nur bei "Standard"-Zugriff (nicht optimiert) sind diese Werte
  überhaupt vorhanden.
- **HMI-Tags bei WinCC Advanced/Comfort: nur `Name` ist über Openness
  abrufbar.** `Datentyp`, `Verbindung` und `Kommentar` bleiben für diesen
  HMI-Typ grundsätzlich leer — die Openness API stellt diese Werte für
  klassische Comfort-/Advanced-Tags schlicht nicht bereit (weder als Property
  noch über `GetAttribute`).
- **WinCC Unified** ist bisher nur anhand der .NET-Typsignaturen verifiziert
  (per Reflection: `HmiUnified.HmiTags.HmiTag` hat echte `DataType`-,
  `Connection`- und `Comment`-Properties), aber noch nicht live gegen ein
  Projekt mit tatsächlichem Unified-Gerät getestet.
- Die Openness-DLL ist versionsgebunden — ein mit V21 angelegtes Projekt lässt
  sich nicht ohne Weiteres mit einer V19-DLL öffnen (siehe `docs/setup-notes.md`).

Details und die konkreten `GetAttributeInfos()`-Ergebnisse siehe
`docs/setup-notes.md`.
