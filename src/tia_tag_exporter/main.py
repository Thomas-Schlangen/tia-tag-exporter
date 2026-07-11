"""Einstiegspunkt für den TIA Tag Exporter — die GUI ist der einzige Weg, das Tool zu starten."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Callable

from config_loader import load_config
from my_logger import setup_logger

from tia_tag_exporter.config_schema import AppConfig
from tia_tag_exporter.connector import TiaConnectionError, TiaConnector
from tia_tag_exporter.exporter import ExcelExporter
from tia_tag_exporter.extractor import DbVariableRecord, HmiTagRecord, PlcTagRecord, TagExtractor
from tia_tag_exporter.project_texts import ProjectTextComments

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("config.yaml")

# TIA Portal V19 (headless/WithoutUserInterface) hat sich in Tests wiederholt
# nicht-deterministisch als instabil erwiesen: die Openness-Session kann
# mitten in der Extraktion sterben (alle Objekte dieser Session werden dann
# disposed — ein Retry auf derselben Session hilft nicht mehr). Reproduziert
# an zwei unabhängigen echten V19-Projekten; bei V21 bisher nicht beobachtet.
# run_export() reagiert darauf mit komplettem Reconnect + Fortsetzen bei den
# noch fehlenden PLCs/DBs/HMI-Targets (siehe dort).
_MAX_RECONNECT_ATTEMPTS = 5


def _configure_console_encoding() -> None:
    """Erzwingt UTF-8 auf stdout/stderr, damit Umlaute in Konsolenausgaben auf
    Windows nicht anhand der lokalen Codepage verstümmelt werden."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _find_software_containers(project: Any, types: tuple[type, ...]) -> list[Any]:
    """Traversiert alle Geräte des Projekts und sammelt die Software-Objekte
    aller Software-Container, deren ``Software`` einer der ``types``
    entspricht.

    Gemeinsame Traversierung für ``_find_plc_software_list`` und
    ``_find_hmi_targets`` — beide liefen zuvor dieselbe
    Devices/DeviceItems/GetService-Schleife fast unverändert.
    """
    from Siemens.Engineering.HW.Features import SoftwareContainer

    result: list[Any] = []
    for device in project.Devices:
        for device_item in device.DeviceItems:
            container = device_item.GetService[SoftwareContainer]()
            if container is not None and isinstance(container.Software, types):
                result.append(container.Software)
    return result


def _find_plc_software_list(project: Any) -> list[Any]:
    """Sammelt alle PLC-Software-Container des Projekts."""
    from Siemens.Engineering.SW import PlcSoftware

    return _find_software_containers(project, (PlcSoftware,))


def _find_hmi_targets(project: Any) -> list[Any]:
    """Sammelt alle HMI-Software-Container des Projekts.

    WinCC Advanced/Comfort und WinCC Unified verwenden unterschiedliche
    Software-Klassen (``Siemens.Engineering.Hmi.HmiTarget`` bzw.
    ``Siemens.Engineering.HmiUnified.HmiSoftware``) — beide werden erfasst,
    sofern die jeweilige Klasse geladen ist (bei V19/V20 stecken beide in der
    monolithischen ``Siemens.Engineering.dll``, bei V21+ in getrennten
    Assemblies). Der ``ImportError``-Fallback ist eine reine Absicherung für
    den Fall, dass eine konkrete Installation eine der beiden Klassen nicht
    bereitstellt.
    """
    from Siemens.Engineering.Hmi import HmiTarget

    hmi_types: tuple[type, ...] = (HmiTarget,)
    try:
        from Siemens.Engineering.HmiUnified import HmiSoftware

        hmi_types = (HmiTarget, HmiSoftware)
    except ImportError:
        logger.debug("WinCC-Unified-Klasse nicht verfügbar — nur Advanced/Comfort wird erfasst.")

    return _find_software_containers(project, hmi_types)


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


def _extract_plc_tags(
    extractor: TagExtractor,
    plc_list: list[Any],
    done_plc_tags: set[str],
) -> list[PlcTagRecord]:
    """Extrahiert PLC-Tags aus allen noch nicht verarbeiteten PLCs in
    ``plc_list``.

    ``done_plc_tags`` wird in-place um die Namen der hier verarbeiteten PLCs
    ergänzt (statt eines Rückgabewerts), damit ein Reconnect in
    ``run_export`` bereits erledigte PLCs bei einem erneuten Aufruf
    überspringt, ohne sie doppelt zu exportieren.
    """
    records: list[PlcTagRecord] = []
    for plc in plc_list:
        plc_name = getattr(plc, "Name", "?")
        if plc_name in done_plc_tags:
            continue
        records.extend(extractor.extract_plc_tags(plc))
        done_plc_tags.add(plc_name)
    return records


def _extract_db_variables(
    extractor: TagExtractor,
    plc_list: list[Any],
    project_texts: ProjectTextComments | None,
    done_dbs: set[tuple[str, str]],
) -> list[DbVariableRecord]:
    """Extrahiert DB-Variablen aus allen noch nicht verarbeiteten
    Datenbausteinen über alle PLCs in ``plc_list``.

    ``done_dbs`` (Schlüssel ``(PLC-Name, DB-Name)``) wird in-place um die
    hier verarbeiteten DBs ergänzt, damit ein Reconnect in ``run_export``
    bereits erledigte DBs bei einem erneuten Aufruf überspringt, ohne sie
    doppelt zu exportieren.
    """
    records: list[DbVariableRecord] = []
    for plc in plc_list:
        plc_name = getattr(plc, "Name", "?")
        for db in _find_data_blocks(plc):
            db_key = (plc_name, getattr(db, "Name", "?"))
            if db_key in done_dbs:
                continue
            records.extend(extractor.extract_db_variables(db, plc, project_texts))
            done_dbs.add(db_key)
    return records


def _extract_hmi_tags(
    extractor: TagExtractor,
    hmi_targets: list[Any],
    project_texts: ProjectTextComments | None,
    done_hmi: set[str],
) -> list[HmiTagRecord]:
    """Extrahiert HMI-Tags aus allen noch nicht verarbeiteten HMI-Targets in
    ``hmi_targets``.

    ``done_hmi`` wird in-place um die Namen der hier verarbeiteten
    HMI-Targets ergänzt, damit ein Reconnect in ``run_export`` bereits
    erledigte HMI-Targets bei einem erneuten Aufruf überspringt, ohne sie
    doppelt zu exportieren.
    """
    records: list[HmiTagRecord] = []
    for hmi in hmi_targets:
        hmi_name = getattr(hmi, "Name", "?")
        if hmi_name in done_hmi:
            continue
        records.extend(extractor.extract_hmi_tags(hmi, project_texts))
        done_hmi.add(hmi_name)
    return records


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

    Verbindet neu und macht bei den noch fehlenden PLCs/DBs/HMI-Targets weiter,
    falls die Openness-Session unerwartet stirbt (siehe ``_MAX_RECONNECT_ATTEMPTS``).
    """

    def report(message: str) -> None:
        logger.info(message)
        if progress is not None:
            progress(message)

    if not (include_plc or include_hmi or include_db):
        raise ValueError("Nichts zu exportieren — bitte mindestens eine Kategorie auswählen.")

    extractor = TagExtractor()
    data: dict[str, list[dict[str, Any]]] = {"plc_tags": [], "hmi_tags": [], "db_variables": []}
    done_plc_tags: set[str] = set()
    done_dbs: set[tuple[str, str]] = set()
    done_hmi: set[str] = set()
    disposed_exc_types: tuple[type, ...] = ()

    for attempt in range(1, _MAX_RECONNECT_ATTEMPTS + 1):
        try:
            if attempt == 1:
                connect_message = "Verbinde mit TIA Portal ..."
            else:
                connect_message = (
                    f"TIA-Portal-Session unerwartet beendet — verbinde neu "
                    f"(Versuch {attempt}/{_MAX_RECONNECT_ATTEMPTS}) ..."
                )
            report(connect_message)

            with TiaConnector(dll_path) as connector:
                project = connector.connect(project_path)
                report(f"Projekt geöffnet: {project.Name}")

                if not disposed_exc_types:
                    try:
                        from Siemens.Engineering import EngineeringObjectDisposedException

                        disposed_exc_types = (EngineeringObjectDisposedException,)
                    except ImportError:
                        disposed_exc_types = (Exception,)

                project_texts = None
                if include_db or include_hmi:
                    # DB-Interface-Member haben kein Comment-Attribut (live verifiziert,
                    # siehe docs/setup-notes.md) — die Kommentare kommen stattdessen aus
                    # der zentralen Projekttexte-Verwaltung. Wird auch für HMI-Tags
                    # gebraucht: deren Kommentar-Fallback ist der Kommentar der
                    # verknüpften PLC-Variable (siehe extract_hmi_tags).
                    report("Lese Projekttexte für Kommentare ...")
                    project_texts = ProjectTextComments.load(project)

                if include_plc or include_db:
                    plc_software_list = _find_plc_software_list(project)
                    report(f"{len(plc_software_list)} PLC-Software-Container gefunden")

                    if include_plc:
                        data["plc_tags"].extend(
                            _extract_plc_tags(extractor, plc_software_list, done_plc_tags)
                        )
                    if include_db:
                        data["db_variables"].extend(
                            _extract_db_variables(extractor, plc_software_list, project_texts, done_dbs)
                        )

                if include_hmi:
                    hmi_targets = _find_hmi_targets(project)
                    report(f"{len(hmi_targets)} HMI-Targets gefunden")
                    data["hmi_tags"].extend(
                        _extract_hmi_tags(extractor, hmi_targets, project_texts, done_hmi)
                    )

            break
        except disposed_exc_types as exc:
            if attempt == _MAX_RECONNECT_ATTEMPTS:
                raise TiaConnectionError(
                    f"TIA-Portal-Verbindung nach {_MAX_RECONNECT_ATTEMPTS} Versuchen "
                    f"weiterhin instabil: {exc}"
                ) from exc
            logger.warning("TIA-Portal-Session unerwartet beendet (Versuch %d): %s", attempt, exc)

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
