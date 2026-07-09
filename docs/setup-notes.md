# Setup-Notizen — TIA Openness DLL

## Suchergebnis (Stand: 2026-07-09)

Gesucht wurde `Siemens.Engineering.dll` in:

- `C:\Program Files\Siemens\Automation\Portal V21\` — **nicht gefunden**
- `C:\Program Files (x86)\Siemens\` — **nicht gefunden**

Stattdessen wurde eine Installation von **TIA Portal V19** gefunden, die mehrere
Openness-API-Versionen als Multi-Targeting-DLLs mitbringt:

```
C:\Program Files\Siemens\Automation\Portal V19\PublicAPI\V16\Siemens.Engineering.dll
C:\Program Files\Siemens\Automation\Portal V19\PublicAPI\V17\Siemens.Engineering.dll
C:\Program Files\Siemens\Automation\Portal V19\PublicAPI\V18\Siemens.Engineering.dll
C:\Program Files\Siemens\Automation\Portal V19\PublicAPI\V19\Siemens.Engineering.dll
```

## Konsequenz für die Konfiguration

Der `dll_path` in `config.toml` muss auf dem aktuellen System auf die **V19**-DLL
zeigen, z. B.:

```toml
dll_path = "C:/Program Files/Siemens/Automation/Portal V19/PublicAPI/V19/Siemens.Engineering.dll"
```

`config.example.toml` verwendet weiterhin einen Beispielpfad für **V21**, da das
Tool laut Aufgabenstellung für V21 ausgelegt ist. Vor dem ersten produktiven Lauf
muss geprüft werden, welche TIA-Portal-Version tatsächlich installiert ist, und
`dll_path` entsprechend angepasst werden (die Openness API ist pro Version an
Portal-Version und PLC-/HMI-Projektversion gebunden — ein Projekt, das mit V21
angelegt wurde, kann nicht ohne Weiteres mit der V19-DLL geöffnet werden).

## Offene Punkte

- [ ] Prüfen, ob auf dem Zielsystem (auf dem TIA Portal V21 tatsächlich installiert
      ist) `Siemens.Engineering.dll` unter dem in `config.example.toml` genannten
      Pfad existiert.
- [ ] `pythonnet` benötigt eine passende .NET-Runtime (i. d. R. .NET Framework 4.8,
      das mit TIA Portal mitinstalliert wird) — auf Kompatibilität prüfen.
