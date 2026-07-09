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

## Offene Punkte

- [ ] Gegen ein reales TIA-Portal-V21-Projekt testen (`GetAttribute`-Strings
      wurden anhand der Namenskonvention der stark typisierten Properties
      abgeleitet, aber nicht live gegen ein geöffnetes Projekt verifiziert —
      eine `.NET`-Reflection ohne laufendes TIA Portal kann keine
      Attribut-Werte, nur Typ-Signaturen prüfen).
- [ ] `pythonnet` benötigt eine passende .NET-Runtime (i. d. R. .NET Framework
      4.8 — alle V21-Assemblies sind `net48`-Builds); auf Kompatibilität mit
      der installierten Python-Version prüfen.
- [ ] Prüfen, ob `Safety`-Datenbausteine (safety-relevante DBs) zusätzliche
      Behandlung benötigen — bisher nicht gesondert berücksichtigt.
