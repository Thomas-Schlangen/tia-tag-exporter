"""CLI-Einstiegspunkt für den TIA Tag Exporter."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any

from loguru import logger

from tia_tag_exporter.connector import TiaConnectionError, TiaConnector
from tia_tag_exporter.exporter import ExcelExporter
from tia_tag_exporter.extractor import TagExtractor


def _configure_console_encoding() -> None:
    """Erzwingt UTF-8 auf stdout/stderr, damit Umlaute in CLI-Texten (z. B. --help) auf
    Windows nicht anhand der lokalen Codepage verstümmelt werden."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _configure_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="<level>{level: <8}</level> | {message}")
    logger.add("export.log", level="DEBUG", rotation="1 MB", retention=5)


def _load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        return {}
    with config_path.open("rb") as handle:
        return tomllib.load(handle)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tia-tag-exporter",
        description="Exportiert PLC-, HMI- und DB-Tags aus einem TIA-Portal-Projekt nach Excel.",
    )
    parser.add_argument("--project", type=Path, help="Pfad zur TIA-Portal-Projektdatei (.ap21 o.ä.)")
    parser.add_argument("--output", type=Path, help="Zielpfad der Excel-Ausgabedatei")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Pfad zur Konfigurationsdatei (Standard: config.toml)",
    )
    parser.add_argument("--plc", action="store_true", help="PLC-Tags exportieren")
    parser.add_argument("--hmi", action="store_true", help="HMI-Tags exportieren")
    parser.add_argument("--db", action="store_true", help="DB-Variablen exportieren")
    return parser.parse_args(argv)


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


def _run(args: argparse.Namespace) -> None:
    config = _load_config(args.config)
    tia_config = config.get("tia", {})
    export_config = config.get("export", {})

    project_path = args.project or (Path(tia_config["project_path"]) if "project_path" in tia_config else None)
    output_path = args.output or (Path(export_config["output_path"]) if "output_path" in export_config else None)
    dll_path = tia_config.get("dll_path")

    if project_path is None:
        raise SystemExit("Kein Projektpfad angegeben (--project oder config.toml [tia].project_path).")
    if output_path is None:
        raise SystemExit("Kein Ausgabepfad angegeben (--output oder config.toml [export].output_path).")
    if not dll_path:
        raise SystemExit("Kein DLL-Pfad angegeben (config.toml [tia].dll_path).")

    include_plc = args.plc or export_config.get("include_plc_tags", False)
    include_hmi = args.hmi or export_config.get("include_hmi_tags", False)
    include_db = args.db or export_config.get("include_db_variables", False)

    if not (include_plc or include_hmi or include_db):
        raise SystemExit("Nichts zu exportieren — bitte --plc, --hmi und/oder --db angeben.")

    extractor = TagExtractor()
    data: dict[str, list[dict[str, Any]]] = {"plc_tags": [], "hmi_tags": [], "db_variables": []}

    with TiaConnector(dll_path) as connector:
        project = connector.connect(project_path)

        if include_plc or include_db:
            plc_software_list = _find_plc_software_list(project)
            logger.info("{} PLC-Software-Container gefunden", len(plc_software_list))

            for plc in plc_software_list:
                if include_plc:
                    data["plc_tags"].extend(extractor.extract_plc_tags(plc))
                if include_db:
                    for db in _find_data_blocks(plc):
                        data["db_variables"].extend(extractor.extract_db_variables(db))

        if include_hmi:
            hmi_targets = _find_hmi_targets(project)
            logger.info("{} HMI-Targets gefunden", len(hmi_targets))
            for hmi in hmi_targets:
                data["hmi_tags"].extend(extractor.extract_hmi_tags(hmi))

    ExcelExporter().export(data, output_path)


def main(argv: list[str] | None = None) -> int:
    _configure_console_encoding()
    _configure_logging()
    args = _parse_args(argv)

    try:
        _run(args)
    except SystemExit as exc:
        logger.error(str(exc))
        return 1
    except TiaConnectionError as exc:
        logger.error("Verbindung zu TIA Portal fehlgeschlagen: {}", exc)
        return 1
    except Exception as exc:  # noqa: BLE001 — letzte Instanz gegen rohe Tracebacks für den Benutzer
        logger.error("Unerwarteter Fehler: {}", exc)
        logger.debug("Details:", exc_info=exc)
        return 1

    logger.success("Export erfolgreich abgeschlossen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
