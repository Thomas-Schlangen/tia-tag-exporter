# Setup-Notizen — TIA Openness DLL (V19–V21)

## Windows-Installation für Endnutzer (Stand: 2026-07-10)

Kurzanleitung für alle, die das Repo klonen oder als ZIP herunterladen und
lokal auf einem Windows-Rechner mit TIA Portal zum Laufen bringen wollen —
auf GitHub liegt nur der Python-Quellcode, keine `.exe`.

1. **Python 3.11+** von [python.org](https://python.org) installieren (nicht
   der Microsoft-Store-Build — der bringt teils ähnliche Einschränkungen mit
   wie Debians `externally-managed-environment`). Im Installer „Add
   python.exe to PATH" aktivieren.
2. Repo klonen (`git clone …`) oder als ZIP herunterladen und entpacken.
3. Optional, aber empfohlen — virtuelle Umgebung anlegen, statt in die
   System-Python-Installation zu installieren:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   ```
4. Installieren:
   ```
   pip install -e .
   ```
   Zieht `pythonnet`, `openpyxl`, `pydantic`, `pyyaml` automatisch nach
   (siehe `pyproject.toml`). Anders als unter Debian/Ubuntu (dort blockiert
   PEP 668 / `externally-managed-environment` ein direktes `pip install`)
   läuft das unter Windows ohne Zusatzflags durch.
5. **TIA Portal (V19/V20/V21) muss lokal installiert sein** — das Tool
   spricht dessen Openness-DLL direkt an. Ohne TIA-Portal-Installation
   funktioniert der Export grundsätzlich nicht, unabhängig von Python.
   Details zum jeweiligen DLL-Layout siehe die folgenden Abschnitte dieser
   Datei.
6. `config.example.yaml` nach `config.yaml` kopieren und `dll_path` auf die
   tatsächliche TIA-Portal-Installation anpassen (siehe unten, "Tatsächliche
   V21-Struktur" bzw. "V19/V20: monolithisches Layout").
7. Starten:
   ```
   tia-tag-exporter
   ```
   Konsolen-Einstiegspunkt aus `pyproject.toml` (`[project.scripts]`) —
   öffnet das Tkinter-GUI, es gibt keinen separaten CLI-Modus.

## Korrektur (Stand: 2026-07-09)

Die erste Suche nach `Siemens.Engineering.dll` in `C:\Program Files\Siemens\`
lieferte nur Treffer für **TIA Portal V19** und keinen Treffer für V21 — **TIA
Portal V21 ist auf diesem System ebenfalls installiert**, unter
`C:\Program Files\Siemens\Automation\Portal V21\`. Es existiert dort nur keine
Datei namens `Siemens.Engineering.dll`.

## Tatsächliche V21-Struktur

Ab V21 ist die Openness API **nicht mehr eine einzelne Assembly**, sondern in
mehrere Assemblies im selben Ordner aufgeteilt:

```
C:\Program Files\Siemens\Automation\Portal V21\PublicAPI\V21\net48\
├── Siemens.Engineering.Base.dll           # TiaPortal, TiaPortalMode, SoftwareContainer — immer benötigt
├── Siemens.Engineering.Step7.dll          # PlcSoftware, PlcTag, DataBlock, Interface-Member (PLC + DB)
├── Siemens.Engineering.WinCC.dll          # HmiTarget (WinCC Advanced/Comfort)
├── Siemens.Engineering.WinCCUnified.dll   # HmiSoftware (WinCC Unified)
├── Siemens.Engineering.Safety.dll
├── Siemens.Engineering.SafetyValidation.dll
├── Siemens.Engineering.ScadaExporter.dll
└── Siemens.Engineering.TeamcenterGateway.dll
```

Verifiziert per .NET-Reflection (`Assembly.LoadFrom` + `GetTypes()`) direkt
gegen die installierten DLLs — nicht nur aus der Dokumentation übernommen.

`config.yaml` / `config.example.yaml` verweisen für V21 daher auf
`Siemens.Engineering.Base.dll`; `TiaConnector._load_dll()` lädt automatisch
zusätzlich `Step7`, `WinCC` und `WinCCUnified` aus demselben Verzeichnis. (Die
Config lief bis 2026-07-09 noch über `config.toml`/`config.example.toml` —
inzwischen auf YAML umgestellt, siehe `src/config_loader`.)

## V19/V20: monolithisches Layout statt Split-Assemblies (Stand: 2026-07-10)

Vor V21 ist die Openness API eine einzige `Siemens.Engineering.dll` direkt
unter `PublicAPI\V19\` (kein `net48`-Unterordner, kein Base/Step7/WinCC-Split).
Zwei Stolpersteine, die live gegen eine echte V19-Installation gefunden
wurden:

- `Siemens.Engineering.dll` hängt von `Siemens.Engineering.Contract.dll` ab,
  die aber **nicht** neben ihr in `PublicAPI\V19\` liegt, sondern unter
  `<Installationswurzel>\Bin\PublicAPI\` (z. B.
  `C:\Program Files\Siemens\Automation\Portal V19\Bin\PublicAPI\`). Ohne
  diesen Pfad im .NET-Assembly-Suchpfad schlägt das Laden von Typen aus
  `Siemens.Engineering` mit einer `FileNotFoundException` auf die
  Contract-Assembly fehl. `TiaConnector._load_dll()` ergänzt diesen Pfad
  automatisch (siehe Kommentar dort).
- Anders als zunächst vermutet enthält die monolithische
  `Siemens.Engineering.dll` **sowohl** `Siemens.Engineering.Hmi.HmiTarget`
  (Advanced/Comfort) **als auch** `Siemens.Engineering.HmiUnified.HmiSoftware`
  (Unified) — Letzteres ist also keine reine V21-Neuerung.

`TiaConnector` erkennt anhand des Dateinamens von `dll_path`
(`Siemens.Engineering.Base.dll` vs. alles andere), welches Layout vorliegt.
Details und die bekannte V19-Headless-Instabilität bei der
DB-Variablen-Extraktion (nicht durch dieses Tool behebbar) siehe README,
Abschnitt "Bekannte Einschränkungen".

## Wichtige Erkenntnisse für die Implementierung (per Reflection verifiziert)

- **DB-Klasse heißt `DataBlock`**, nicht `DB`. Konkrete Typen sind `GlobalDB`,
  `InstanceDB`, `ArrayDB` — alle leiten von
  `Siemens.Engineering.SW.Blocks.DataBlock` ab.
- **DB-Interface-Member (`Siemens.Engineering.SW.Blocks.Interface.Member`)
  haben keine stark typisierten Properties** für Datentyp/Offset/Kommentar/
  Initialwert — nur `Name` und `Parent` sind echte Properties. Die übrigen
  Werte müssen über `member.GetAttribute("DataTypeName")` etc. gelesen werden.
  Verschachtelte Struct-Member liegen hinter einer **expliziten
  Interface-Implementierung** (`IEngineeringObject.GetComposition("Members")`),
  nicht hinter einer normalen `.Members`-Property.
- **WinCC Advanced/Comfort (`Siemens.Engineering.Hmi.HmiTarget`)** organisiert
  Tag-Tabellen rekursiv unter `hmi.TagFolder` (`.TagTables` + `.Folders`), nicht
  direkt am HMI-Objekt. Die Tag-Klasse `Siemens.Engineering.Hmi.Tag.Tag` hat
  ebenfalls kaum stark typisierte Properties — Datentyp/Verbindung/Kommentar
  laufen über `GetAttribute`.
- **WinCC Unified (`Siemens.Engineering.HmiUnified.HmiSoftware`)** ist ein
  komplett anderer Objektgraph als Advanced/Comfort (kein gemeinsamer
  `HmiTarget`-Basistyp) — `hmi.TagTables` liefert dort aber direkt eine flache
  Liste, und die Tag-Klasse (`HmiUnified.HmiTags.HmiTag`) hat echte Properties
  (`DataType`, `Connection`, `Comment`).
- `PlcTag` (PLC-Tags) und `PlcSoftware`/`PlcTagTableGroup`/`PlcBlockGroup`
  (Navigation) sind dagegen wie ursprünglich angenommen stark typisiert
  (`Name`, `DataTypeName`, `LogicalAddress`, `Comment`,
  `ExternalAccessible/Visible/Writable`).

`extractor.py` und `main.py` wurden entsprechend angepasst (siehe Git-Historie).

## Live-Test gegen reales Projekt (Stand: 2026-07-09)

Getestet gegen `xxxxx` (Kunde, 183 DBs, 2 HMI-Targets, alles TIA V21).
Ergebnis: **435 PLC-Tags**, **~104.000 DB-Variablen**, **1085 HMI-Tags**
korrekt exportiert. Dabei zwei zusätzliche Bugs gefunden und behoben:

- `ProjectComposition.Open(string)` existiert nicht — die Methode erwartet
  ein `System.IO.FileInfo`-Objekt, kein `str`. `connector.py` konstruiert das
  jetzt über `from System.IO import FileInfo`.
- Windows-Konsolenausgabe (`argparse --help` u. a.) zeigte Umlaute
  verstümmelt an, weil `stdout`/`stderr` beim Pipen nicht UTF-8 sondern die
  lokale Codepage nutzen. `main.py` erzwingt jetzt
  `sys.stdout.reconfigure(encoding="utf-8")` beim Start.

Außerdem zwei **echte, live verifizierte Grenzen der Openness API** gefunden
(kein Bug im Tool, aber wichtig für Nutzer):

- **DB-Interface-Member: `Offset` und `Comment` sind bei optimierten
  Bausteinen (Optimized Block Access) nicht verfügbar.** `member.GetAttributeInfos()`
  listet für ein Optimized-DB-Member nur `DataTypeName, DefaultValue,
  ExternalAccessible, ExternalVisible, ExternalWritable, Name, Retain,
  Setpoint, Snapshot, StartValue` — kein `Offset`, kein `Comment`. Optimierte
  Bausteine sind seit vielen TIA-Versionen der Standard (TIA verwaltet das
  Speicherlayout intern, ein fester Byte-Offset existiert dafür schlicht
  nicht). Im echten Testprojekt waren alle 183 DBs optimiert — die
  Offset-/Kommentar-Spalte bleibt dort für jede Zeile leer. Das ist korrektes
  Verhalten, kein Extraktionsfehler.
- **WinCC Advanced/Comfort HMI-Tags (`Siemens.Engineering.Hmi.Tag.Tag`)
  exponieren über Openness ausschließlich `Name`.** `tag.GetAttributeInfos()`
  liefert nur `Name` — `DataType`, `Connection`, `Comment`, `Address` sind für
  diesen Typ nicht abrufbar (weder als Property noch über `GetAttribute`).
  Im Testprojekt waren beide gefundenen HMI-Targets vom Typ Advanced/Comfort,
  daher blieben Datentyp/Verbindung/Kommentar in der HMI-Tags-Tabelle leer.
  Das ist eine bekannte, harte Einschränkung der Openness API für klassische
  HMI-Tags (Comfort/Advanced) — kein Bug in `extractor.py`.

## Live-Test gegen reales V19-Projekt (Stand: 2026-07-11)

Getestet gegen `xxxxx` (Kunde, 283 DBs, 0 HMI-Targets, TIA V19). Ergebnis:
**1.791 PLC-Tags**, **291.195 DB-Variablen** korrekt exportiert, Laufzeit
~11 Minuten. Kein einziger Reconnect nötig — die in `README.md` dokumentierte
V19-Headless-Instabilität (`EngineeringObjectDisposedException` mitten in der
DB-Extraktion) ist bei diesem Lauf nicht aufgetreten. Damit sind jetzt drei
unabhängige reale V19-Projekte getestet: zwei mit dem beschriebenen
Instabilitätsmuster, eines (dieses) komplett sauber durchgelaufen — passt
zum bereits dokumentierten nicht-deterministischen Charakter des Problems
(kein Bug im Tool, siehe README). Der automatische Reconnect
(`_MAX_RECONNECT_ATTEMPTS` in `main.py`) wäre bei Bedarf gegriffen, war hier
aber nicht erforderlich.

## Live-Test gegen reales WinCC-Unified-Projekt (Stand: 2026-07-11)

Getestet gegen `xxxxx` (Kunde, TIA V21, 4 WinCC-Unified-HMI-Targets).
Ergebnis: **502 PLC-Tags**, **73.704 DB-Variablen**, **316 HMI-Tags** korrekt
exportiert, Laufzeit ~3,5 Minuten, kein Reconnect nötig. Damit ist die bisher
nur per Reflection verifizierte Unified-Unterstützung jetzt auch live
bestätigt: `Datentyp`/`Verbindung` werden wie erwartet direkt aus echten
Properties gelesen, `PLC-Variable` (ControllerTag) wird für verlinkte Tags
korrekt aufgelöst.

Zwei Beobachtungen, keine davon ein Bug im Tool:

- Für System-Tag-Tabellen (`Standard-Variablentabelle`, `ColorTags`,
  `SessionLocal`, `TEST`) schlägt der `HmiTagTable.Export()`-Aufruf in
  `_read_hmi_tag_links()` fehl (`'HmiTagTable' object has no attribute
  'Export'`). Der bestehende `except`-Fallback fängt das wie dokumentiert ab —
  nur `PLC-Variable`/`Datentyp`/`Verbindung` bleiben für diese Tabellen leer,
  kein Absturz.
- Ein Teil der Kommentare/Quellkommentare enthält kaputte Zeichen (`�` statt
  `ü`/`ö`, z. B. `"Hilfsvariable Skript�berlast"`). Kommt unverändert aus
  `Project.ExportProjectTexts()` (siehe `project_texts.py`) — TIA Portal
  liefert die Zeichen bereits so im Export, das Tool liest nur mit `openpyxl`
  weiter, ohne eigene Encoding-Logik dazwischen. Sieht nach einer bereits im
  Testprojekt vorhandenen Zeichensatz-Inkonsistenz aus, nicht nach einem Fehler
  in `project_texts.py` oder `extractor.py`.

## Offene Punkte

- [x] WinCC **Unified** (`HmiSoftware`/`HmiUnified.HmiTags.HmiTag`) — live
      gegen ein reales Projekt mit vier tatsächlichen WinCC-Unified-Geräten
      getestet (siehe Abschnitt oben), nicht mehr nur per Reflection
      verifiziert.
- [x] `pythonnet` benötigt eine passende .NET-Runtime (i. d. R. .NET Framework
      4.8 — alle V21-Assemblies sind `net48`-Builds); auf Kompatibilität mit
      der installierten Python-Version prüfen. In der Praxis durch die beiden
      Live-Tests oben bereits bestätigt: Python 3.12 + `pythonnet` 3.x liefen
      sowohl gegen das monolithische V19-Layout als auch gegen die V21
      `net48`-Split-Assemblies fehlerfrei durch (Stand: 2026-07-11).
- [ ] Prüfen, ob `Safety`-Datenbausteine (safety-relevante DBs) zusätzliche
      Behandlung benötigen — im Testprojekt wurden Safety-DBs (`DataToSafety`,
      `DataFromSafety` etc.) ohne Sonderbehandlung korrekt mit ausgelesen.
- [x] `TagExtractor._get_db_folder_path()` war live komplett kaputt (lieferte
      immer eine leere Liste, daher blieb die spätere "Pfad"-Spalte im Excel
      leer) — behoben am 2026-07-10, siehe Docstring der Methode. Zwei
      Ursachen, beide durch dasselbe pythonnet-Verhalten: `db.Parent` liefert
      Objekte, die pythonnet nur als generisches `IEngineeringObject`-Interface
      typisiert, nicht als konkrete Klasse. Dadurch war (a)
      `getattr(node, "Name", None)` immer `None` (Fix: `GetAttribute("Name")`)
      und (b) `isinstance(node, PlcSoftware)` hat nie gematcht, wodurch die
      Schleife bis zur `TiaPortal`-Wurzel gelaufen wäre (Fix:
      `node.Equals(plc.BlockGroup)` als Abbruchbedingung — dafür braucht
      `_get_db_folder_path()` jetzt zusätzlich das `plc`-Objekt als Parameter).
      Live gegen ein reales Projekt mit mehrstufiger Ordnerstruktur verifiziert
      (49.452 DB-Variablen-Zeilen, 0 mit leerem Pfad).
