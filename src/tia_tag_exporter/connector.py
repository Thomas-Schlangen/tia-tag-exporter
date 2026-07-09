"""Verbindung zur TIA Portal Openness API über pythonnet."""

from __future__ import annotations

import sys
from pathlib import Path
from types import TracebackType

from loguru import logger


class TiaConnectionError(RuntimeError):
    """Wird ausgelöst, wenn die Verbindung zu TIA Portal fehlschlägt."""


class TiaConnector:
    """Kapselt den Zugriff auf die TIA Portal Openness API.

    Lädt die Openness-.NET-Assembly per ``pythonnet`` und öffnet ein
    TIA-Portal-Projekt im Headless-Modus (``TiaPortalMode.WithoutUserInterface``).
    """

    def __init__(self, dll_path: str | Path) -> None:
        self.dll_path = Path(dll_path)
        self._tia_portal = None
        self._project = None
        self._clr_loaded = False

    def _load_dll(self) -> None:
        if self._clr_loaded:
            return

        if not self.dll_path.is_file():
            raise TiaConnectionError(
                f"Siemens.Engineering.dll wurde unter '{self.dll_path}' nicht gefunden."
            )

        import clr  # pythonnet

        sys.path.append(str(self.dll_path.parent))
        clr.AddReference(str(self.dll_path.stem))
        self._clr_loaded = True
        logger.debug("Siemens.Engineering.dll geladen von {}", self.dll_path)

    def connect(self, project_path: str | Path):
        """Öffnet ein TIA-Portal-Projekt und gibt das Projekt-Objekt zurück."""
        project_path = Path(project_path)
        if not project_path.is_file():
            raise TiaConnectionError(f"Projektdatei nicht gefunden: {project_path}")

        self._load_dll()

        from Siemens.Engineering import TiaPortal, TiaPortalMode  # noqa: E402

        logger.info("Öffne TIA Portal (Headless) für Projekt: {}", project_path)
        self._tia_portal = TiaPortal(TiaPortalMode.WithoutUserInterface)

        try:
            project_composition = self._tia_portal.Projects
            self._project = project_composition.Open(str(project_path))
        except Exception as exc:  # noqa: BLE001 — Openness wirft .NET-Exceptions
            self.disconnect()
            raise TiaConnectionError(f"Projekt konnte nicht geöffnet werden: {exc}") from exc

        logger.info("Projekt erfolgreich geöffnet: {}", self._project.Name)
        return self._project

    def disconnect(self) -> None:
        """Schließt das Projekt und beendet die TIA-Portal-Instanz."""
        if self._project is not None:
            try:
                self._project.Close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Fehler beim Schließen des Projekts: {}", exc)
            self._project = None

        if self._tia_portal is not None:
            try:
                self._tia_portal.Dispose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Fehler beim Beenden von TIA Portal: {}", exc)
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
