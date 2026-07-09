"""Export von Tag-Daten in eine formatierte Excel-Datei."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

SHEET_NAMES: dict[str, str] = {
    "plc_tags": "PLC-Tags",
    "hmi_tags": "HMI-Tags",
    "db_variables": "DB-Variablen",
}


class ExcelExporter:
    """Schreibt extrahierte Tag-Daten als formatierte Excel-Arbeitsmappe."""

    def export(self, data: dict[str, list[dict[str, Any]]], output_path: Path) -> None:
        """Erstellt eine Excel-Datei mit einem Sheet pro Kategorie.

        Args:
            data: Mapping von Kategorie-Schlüssel (``plc_tags``, ``hmi_tags``,
                ``db_variables``) auf eine Liste von Records (Dicts mit gleichen Keys).
            output_path: Zielpfad der ``.xlsx``-Datei.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        workbook = Workbook()
        workbook.remove(workbook.active)

        for key, sheet_name in SHEET_NAMES.items():
            records = data.get(key)
            if not records:
                logger.debug("Keine Daten für Kategorie '{}', Sheet wird übersprungen", key)
                continue
            sheet = workbook.create_sheet(title=sheet_name)
            self._write_sheet(sheet, records)

        if not workbook.sheetnames:
            workbook.create_sheet(title="Leer")
            logger.warning("Keine Daten zum Exportieren vorhanden — leere Datei wird erzeugt")

        workbook.save(output_path)
        logger.info("Excel-Datei geschrieben: {}", output_path)

    @staticmethod
    def _write_sheet(sheet: Worksheet, records: list[dict[str, Any]]) -> None:
        headers = list(records[0].keys())
        sheet.append(headers)

        header_font = Font(bold=True)
        for cell in sheet[1]:
            cell.font = header_font

        for record in records:
            sheet.append([record.get(header, "") for header in headers])

        sheet.freeze_panes = "A2"

        for col_index, header in enumerate(headers, start=1):
            max_length = len(str(header))
            for record in records:
                value = record.get(header, "")
                max_length = max(max_length, len(str(value)))
            sheet.column_dimensions[get_column_letter(col_index)].width = min(max_length + 2, 60)
