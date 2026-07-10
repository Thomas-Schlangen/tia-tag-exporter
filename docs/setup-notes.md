# Setup-Notizen — TIA Openness DLL (V21)

## Korrektur (Stand: 2026-07-09)

Die erste Suche nach `Siemens.Engineering.dll` in `C:\Program Files\Siemens\`
lieferte nur Treffer für **TIA Portal V19** und keinen Treffer für V21 — das
war jedoch ein Suchfehler, kein fehlendes Feature: **TIA Portal V21 ist auf
diesem System ebenfalls installiert**, unter
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

`config.toml` / `config.example.toml` verweisen daher auf
`Siemens.Engineering.Base.dll`; `TiaConnector._load_dll()` lädt automatisch
zusätzlich `Step7`, `WinCC` und `WinCCUnified` aus demselben Verzeichnis.

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

Getestet gegen `S7T0160` (Pakeeza, 183 DBs, 2 HMI-Targets, alles TIA V21).
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

## Offene Punkte

- [ ] WinCC **Unified** (`HmiSoftware`/`HmiUnified.HmiTags.HmiTag`) ist bisher
      nur per Reflection (Typsignaturen: echte Properties `DataType`,
      `Connection`, `Comment` vorhanden) verifiziert, aber noch nicht live
      gegen ein Projekt mit tatsächlichem WinCC-Unified-Gerät getestet.
      Kandidat vorhanden: `D:\Daten\Tmp\ProjektNilay\WinCCUnified_V21\`.
- [ ] `pythonnet` benötigt eine passende .NET-Runtime (i. d. R. .NET Framework
      4.8 — alle V21-Assemblies sind `net48`-Builds); auf Kompatibilität mit
      der installierten Python-Version prüfen.
- [ ] Prüfen, ob `Safety`-Datenbausteine (safety-relevante DBs) zusätzliche
      Behandlung benötigen — im Testprojekt wurden Safety-DBs (`DataToSafety`,
      `DataFromSafety` etc.) ohne Sonderbehandlung korrekt mit ausgelesen.
- [ ] `TagExtractor._get_db_folder_path()` (Ordnerpfad eines DBs für die
      neuen `_folder_path`/`_db_name`-Felder) ist bisher nur anhand der
      dokumentierten Openness-Objektmodelle entworfen (Klettern der
      `Parent`-Kette von `PlcBlockUserGroup`/`PlcBlockGroup` bis zur
      `PlcSoftware`, danach der Gerätename aus deren `Parent`-`DeviceItem`),
      aber noch **nicht live gegen ein Projekt mit mehrstufiger
      DB-Ordnerstruktur verifiziert**. Insbesondere unklar: ob die Wurzel-
      `PlcBlockGroup` (`plc.BlockGroup`) tatsächlich einen sinnvollen `Name`
      wie "Programmbausteine" liefert oder eine leere Zeichenkette.
