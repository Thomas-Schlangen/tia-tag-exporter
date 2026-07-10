"""Verbindung zur TIA Portal Openness API über pythonnet."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import TracebackType

logger = logging.getLogger(__name__)


class TiaConnectionError(RuntimeError):
    """Wird ausgelöst, wenn die Verbindung zu TIA Portal fehlschlägt."""


# Ab TIA Portal V21 ist die Openness API nicht mehr eine einzelne
# "Siemens.Engineering.dll", sondern in mehrere Assemblies im selben
# net48-Ordner aufgeteilt. "Base" enthält TiaPortal/TiaPortalMode und muss
# zuerst geladen werden, die übrigen liefern PLC- (Step7) bzw. HMI-Zugriff
# (WinCC = Advanced/Comfort, WinCCUnified = WinCC Unified).
_SPLIT_LAYOUT_ASSEMBLIES = (
    "Siemens.Engineering.Base",
    "Siemens.Engineering.Step7",
    "Siemens.Engineering.WinCC",
    "Siemens.Engineering.WinCCUnified",
)

# Vor V21 (V19/V20) ist die Openness API eine einzige "Siemens.Engineering.dll"
# (PLC/DB/Base *und* HmiUnified zusammen) plus optional "Siemens.Engineering.Hmi.dll"
# für WinCC Advanced/Comfort. "Siemens.Engineering.dll" hängt von
# "Siemens.Engineering.Contract.dll" ab, die nicht im selben Ordner liegt
# (siehe _load_dll).
_MONOLITHIC_LAYOUT_ASSEMBLIES = (
    "Siemens.Engineering",
    "Siemens.Engineering.Hmi",
)


class TiaConnector:
    """Kapselt den Zugriff auf die TIA Portal Openness API.

    Lädt die Openness-.NET-Assemblies per ``pythonnet`` und öffnet ein
    TIA-Portal-Projekt im Headless-Modus (``TiaPortalMode.WithoutUserInterface``).
    """

    def __init__(self, dll_path: str | Path) -> None:
        """``dll_path`` zeigt entweder auf ``Siemens.Engineering.Base.dll``
        (V21+, Split-Layout) oder auf ``Siemens.Engineering.dll`` (V19/V20,
        monolithisches Layout) — anhand des Dateinamens wird automatisch
        erkannt, welche weiteren Assemblies aus demselben Ordner nachgeladen
        werden."""
        self.dll_path = Path(dll_path)
        self._tia_portal = None
        self._project = None
        self._clr_loaded = False

    def _load_dll(self) -> None:
        if self._clr_loaded:
            return

        if not self.dll_path.is_file():
            raise TiaConnectionError(
                f"Openness-Assembly wurde unter '{self.dll_path}' nicht gefunden."
            )

        import clr  # pythonnet

        assembly_dir = self.dll_path.parent
        sys.path.append(str(assembly_dir))

        is_split_layout = self.dll_path.stem == "Siemens.Engineering.Base"
        assembly_names = (
            _SPLIT_LAYOUT_ASSEMBLIES if is_split_layout else _MONOLITHIC_LAYOUT_ASSEMBLIES
        )

        if not is_split_layout:
            # Bei V19/V20 hängt Siemens.Engineering.dll von
            # Siemens.Engineering.Contract.dll ab, die dort nicht neben der
            # Haupt-Assembly liegt, sondern unter "<Installationswurzel>\Bin\PublicAPI"
            # (Installationswurzel = drei Ebenen über <dll_path>, z. B.
            # ".../Portal V19/PublicAPI/V19/Siemens.Engineering.dll" ->
            # ".../Portal V19"). Ohne diesen Pfad schlägt das Laden von Typen
            # aus Siemens.Engineering mit einer FileNotFoundException auf die
            # Contract-Assembly fehl.
            try:
                install_root = self.dll_path.parents[2]
            except IndexError:
                install_root = None
            if install_root is not None:
                contract_dir = install_root / "Bin" / "PublicAPI"
                if contract_dir.is_dir():
                    sys.path.append(str(contract_dir))

        for assembly_name in assembly_names:
            assembly_path = assembly_dir / f"{assembly_name}.dll"
            if not assembly_path.is_file():
                logger.warning(
                    "Optionale Openness-Assembly nicht gefunden, wird übersprungen: %s",
                    assembly_path,
                )
                continue
            clr.AddReference(assembly_name)
            logger.debug("Assembly geladen: %s", assembly_path)

        self._clr_loaded = True

    def connect(self, project_path: str | Path):
        """Öffnet ein TIA-Portal-Projekt und gibt das Projekt-Objekt zurück."""
        project_path = Path(project_path)
        if not project_path.is_file():
            raise TiaConnectionError(f"Projektdatei nicht gefunden: {project_path}")

        self._load_dll()

        from Siemens.Engineering import TiaPortal, TiaPortalMode  # noqa: E402
        from System.IO import FileInfo  # noqa: E402

        logger.info("Öffne TIA Portal (Headless) für Projekt: %s", project_path)
        self._tia_portal = TiaPortal(TiaPortalMode.WithoutUserInterface)

        try:
            project_composition = self._tia_portal.Projects
            self._project = project_composition.Open(FileInfo(str(project_path)))
        except Exception as exc:  # noqa: BLE001 — Openness wirft .NET-Exceptions
            self.disconnect()
            raise TiaConnectionError(f"Projekt konnte nicht geöffnet werden: {exc}") from exc

        logger.info("Projekt erfolgreich geöffnet: %s", self._project.Name)
        return self._project

    def disconnect(self) -> None:
        """Schließt das Projekt und beendet die TIA-Portal-Instanz."""
        if self._project is not None:
            try:
                self._project.Close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Fehler beim Schließen des Projekts: %s", exc)
            self._project = None

        if self._tia_portal is not None:
            try:
                self._tia_portal.Dispose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Fehler beim Beenden von TIA Portal: %s", exc)
            self._tia_portal = None

    def __enter__(self) -> "TiaConnector":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.disconnect()

    @property
    def project(self):
        return self._project
