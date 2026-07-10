"""Einstiegspunkt für den TIA Tag Exporter — die GUI ist der einzige Weg, das Tool zu starten."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Callable

from config_loader import load_config
from my_logger import setup_logger

from tia_tag_exporter.config_schema import AppConfig
from tia_tag_exporter.connector import TiaConnector
from tia_tag_exporter.exporter import ExcelExporter
from tia_tag_exporter.extractor import TagExtractor

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config.yaml")


def _configure_console_encoding() -> None:
    """Erzwingt UTF-8 auf stdout/stderr, damit Umlaute in Konsolenausgaben auf
    Windows nicht anhand der lokalen Codepage verstümmelt werden."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _find_plc_software_list(project: Any) -> list[Any]:
    """Traversiert alle Geräte des Projekts und sammelt PLC-Software-Container."""
    from Siemens.Engineering.HW.Features import SoftwareContainer
    from Siemens.Engineering.SW import PlcSoftware

    result: list[Any] = []
    for device in project.Devices:
        for device_item in device.DeviceItems:
            container = device_item.GetService[SoftwareContainer]()
            if container is not None and isinstance(container.Software, PlcSoftware):
                result.append(container.Software)
    return result


def _find_hmi_targets(project: Any) -> list[Any]:
    """Traversiert alle Geräte des Projekts und sammelt HMI-Software-Container.

    WinCC Advanced/Comfort und WinCC Unified verwenden unterschiedliche
    Software-Klassen (``Siemens.Engineering.Hmi.HmiTarget`` bzw.
    ``Siemens.Engineering.HmiUnified.HmiSoftware``) — beide werden erfasst.
    """
    from Siemens.Engineering.HW.Features import SoftwareContainer
    from Siemens.Engineering.Hmi import HmiTarget
    from Siemens.Engineering.HmiUnified import HmiSoftware

    result: list[Any] = []
    for device in project.Devices:
        for device_item in device.DeviceItems:
            container = device_item.GetService[SoftwareContainer]()
            if container is not None and isinstance(container.Software, (HmiTarget, HmiSoftware)):
                result.append(container.Software)
    return result


def _find_data_blocks(plc_software: Any) -> list[Any]:
    """Sammelt rekursiv alle Datenbausteine (Data Blocks) einer PLC-Software.

    Openness kennt keine Klasse namens ``DB`` — Datenbausteine (Global-DB,
    Instanz-DB, Array-DB) leiten alle von ``Siemens.Engineering.SW.Blocks.DataBlock`` ab.
    """
    from Siemens.Engineering.SW.Blocks import DataBlock

    result: list[Any] = []

    def _walk(block_group: Any) -> None:
        for block in block_group.Blocks:
            if isinstance(block, DataBlock):
                result.append(block)
        for subgroup in getattr(block_group, "Groups", []):
            _walk(subgroup)

    _walk(plc_software.BlockGroup)
    return result


def run_export(
    dll_path: str,
    project_path: Path,
    output_path: Path,
    include_plc: bool,
    include_hmi: bool,
    include_db: bool,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Führt den vollständigen Export durch (Verbindung, Extraktion, Excel-Schreiben).

    Wird von der GUI in einem separaten Thread aufgerufen; ``progress`` erhält
    Statusmeldungen für die Anzeige in der Statuszeile.
    """

    def report(message: str) -> None:
        logger.info(message)
        if progress is not None:
            progress(message)

    if not (include_plc or include_hmi or include_db):
        raise ValueError("Nichts zu exportieren — bitte mindestens eine Kategorie auswählen.")

    extractor = TagExtractor()
    data: dict[str, list[dict[str, Any]]] = {"plc_tags": [], "hmi_tags": [], "db_variables": []}

    report("Verbinde mit TIA Portal ...")
    with TiaConnector(dll_path) as connector:
        project = connector.connect(project_path)
        report(f"Projekt geöffnet: {project.Name}")

        if include_plc or include_db:
            plc_software_list = _find_plc_software_list(project)
            report(f"{len(plc_software_list)} PLC-Software-Container gefunden")

            for plc in plc_software_list:
                if include_plc:
                    data["plc_tags"].extend(extractor.extract_plc_tags(plc))
                if include_db:
                    for db in _find_data_blocks(plc):
                        data["db_variables"].extend(extractor.extract_db_variables(db))

        if include_hmi:
            hmi_targets = _find_hmi_targets(project)
            report(f"{len(hmi_targets)} HMI-Targets gefunden")
            for hmi in hmi_targets:
                data["hmi_tags"].extend(extractor.extract_hmi_tags(hmi))

    report("Schreibe Excel-Datei ...")
    ExcelExporter().export(data, output_path)
    report(f"Export erfolgreich abgeschlossen: {output_path}")


def load_app_config(config_path: Path = CONFIG_PATH) -> AppConfig:
    if not config_path.is_file():
        raise SystemExit(
            f"Konfigurationsdatei nicht gefunden: {config_path} "
            f"({config_path.name} aus config.example.yaml erstellen und anpassen)"
        )
    return load_config(config_path, AppConfig)


def main() -> int:
    _configure_console_encoding()

    try:
        config = load_app_config()
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — letzte Instanz gegen rohe Tracebacks für den Benutzer
        print(f"Fehler beim Laden der Konfiguration: {exc}", file=sys.stderr)
        return 1

    setup_logger(config.logging)
    logger.info("Konfiguration geladen, starte GUI")

    from tia_tag_exporter.gui import TiaTagExporterApp

    app = TiaTagExporterApp(config, run_export)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
