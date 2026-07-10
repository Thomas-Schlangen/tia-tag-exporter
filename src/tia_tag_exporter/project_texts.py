"""Liest DB-Variablen- und HMI-Tag-Kommentare aus der zentralen
TIA-Portal-Projekttexte-Verwaltung.

Sowohl ``Siemens.Engineering.SW.Blocks.Interface.Member`` (DB-Variablen) als
auch ``Siemens.Engineering.Hmi.Tag.Tag`` (HMI-Variablen bei WinCC
Advanced/Comfort) haben über Openness kein ``Comment``-Attribut — live
erschöpfend verifiziert über alle Member-/Tag-Typen eines realen Projekts
(siehe docs/setup-notes.md). Die Kommentare existieren aber sehr wohl im
Projekt: TIA Portal verwaltet sie zentral unter "Sprachen & Ressourcen >
Projekttexte", exportierbar über ``Project.ExportProjectTexts()``. Dieses
Modul exportiert die Projekttexte einmalig in eine temporäre Excel-Datei und
baut daraus zwei Nachschlage-Tabellen.

**DB-Variablen** — Zeilen der Kategorie ``<BlockCommentCategoryData>`` mit
einem ``ViewPath`` wie
``{Projekt}\\{PLC}\\Programmbausteine\\...\\{DB-Name}\\{Membername}`` (bei
verschachtelten Struct-Membern mit Punktnotation, z. B. ``4805_30M1.Drive`` —
identisch zur eigenen ``full_name``-Konvention in ``extractor.py``). Der
PLC-Name (zweites Pfadsegment, direkt nach dem Projektnamen) wird mit in den
Schlüssel aufgenommen — DB-Namen sind zwar innerhalb einer PLC eindeutig,
aber nicht projektweit über mehrere PLCs hinweg, und der ``ViewPath`` enthält
den PLC-Namen ohnehin bereits explizit. Andere Zeilen derselben Kategorie
(Bausteinkommentare, Netzwerkkommentare im Code, UDT-Kommentare) landen
ebenfalls im internen Dict, matchen aber nie einen echten
(PLC-Name, DB-Name, Variablenname)-Schlüssel und werden schlicht nie abgefragt.

**HMI-Variablen** — Zeilen der Kategorie ``<HMI comment>`` mit einem
``ViewPath`` wie
``{Projekt}\\{HMI-Gerät}\\HMI-Variablen\\{Tag-Tabelle}\\{Tag-Name}\\Kommentar``
(live gefunden und verifiziert, z. B.
``...\\HMI-Variablen\\internal\\blnPLC1SwitchUpdated\\Kommentar``).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import openpyxl

logger = logging.getLogger(__name__)

_BLOCK_COMMENT_CATEGORY = "<BlockCommentCategoryData>"
_HMI_COMMENT_CATEGORY = "<HMI comment>"


class ProjectTextComments:
    """Nachschlage-Tabelle für DB-Variablen- und HMI-Tag-Kommentare aus den Projekttexten."""

    def __init__(self) -> None:
        self._comments: dict[tuple[str, str, str], str] = {}
        self._comments_by_db_member: dict[tuple[str, str], str] = {}
        self._hmi_comments: dict[tuple[str, str, str], str] = {}

    @classmethod
    def load(cls, project: Any) -> "ProjectTextComments":
        """Exportiert die Projekttexte einmalig und baut die Nachschlage-Tabelle auf.

        Schlägt der Export oder das Einlesen fehl (z. B. kein Schreibzugriff
        auf ein Temp-Verzeichnis), wird eine leere Instanz zurückgegeben —
        DB-Variablen-Kommentare bleiben dann leer, der restliche Export läuft
        unbeeinträchtigt weiter.
        """
        instance = cls()
        try:
            instance._load_from_project(project)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Projekttexte konnten nicht gelesen werden, DB-Variablen-Kommentare bleiben leer: %s",
                exc,
            )
        return instance

    def _load_from_project(self, project: Any) -> None:
        from System.IO import FileInfo

        language = project.LanguageSettings.ReferenceLanguage.Culture

        with tempfile.TemporaryDirectory() as tmp_dir:
            export_path = Path(tmp_dir) / "project_texts.xlsx"
            project.ExportProjectTexts(FileInfo(str(export_path)), language, language)

            workbook = openpyxl.load_workbook(export_path, read_only=True, data_only=True)
            try:
                sheet = workbook["User Texts"]
                rows = sheet.iter_rows(values_only=True)
                header = next(rows)
                category_idx = header.index("Category")
                view_path_idx = header.index("ViewPath")
                # Zwei Sprachspalten pro exportierter Sprache: "<lang>*" (Referenz)
                # und "<lang>" (Ziel) — bei source == target identisch befüllt,
                # ansonsten wird genommen, was vorhanden ist.
                lang_col_indices = [
                    i for i, name in enumerate(header) if isinstance(name, str) and str(language.Name) in name
                ]

                for row in rows:
                    category = row[category_idx]
                    if category not in (_BLOCK_COMMENT_CATEGORY, _HMI_COMMENT_CATEGORY):
                        continue
                    view_path = row[view_path_idx]
                    if not view_path:
                        continue
                    text = next((row[i] for i in lang_col_indices if row[i]), None)
                    if not text:
                        continue

                    segments = view_path.split("\\")

                    if category == _BLOCK_COMMENT_CATEGORY:
                        if len(segments) < 3:
                            continue
                        plc_name = segments[1]
                        db_name = segments[-2]
                        member_path = segments[-1]
                        self._comments[(plc_name, db_name, member_path)] = text
                        self._comments_by_db_member[(db_name, member_path)] = text
                    else:  # _HMI_COMMENT_CATEGORY
                        # ViewPath endet auf "...\<Tag-Tabelle>\<Tag-Name>\Kommentar"
                        if len(segments) < 4:
                            continue
                        hmi_name = segments[1]
                        table_name = segments[-3]
                        tag_name = segments[-2]
                        self._hmi_comments[(hmi_name, table_name, tag_name)] = text
            finally:
                workbook.close()

        logger.info(
            "%d DB-Variablen-Kommentare und %d HMI-Tag-Kommentare aus Projekttexten geladen",
            len(self._comments),
            len(self._hmi_comments),
        )

    def get(self, plc_name: str, db_name: str, member_path: str) -> str | None:
        """Liefert den Kommentar für ``plc_name``/``db_name``/``member_path``
        (Punktnotation bei verschachtelten Membern), oder ``None`` falls keiner
        hinterlegt ist."""
        return self._comments.get((plc_name, db_name, member_path))

    def get_by_db_member(self, db_name: str, member_path: str) -> str | None:
        """Wie ``get()``, aber ohne PLC-Namen im Schlüssel.

        Für HMI-Tags: Die verknüpfte PLC-Variable (siehe
        ``TagExtractor._read_controller_tags``) ist nur als ``DB.Member``-Pfad
        bekannt, ohne Angabe, zu welcher PLC dieser DB gehört. Bei mehreren
        PLCs mit einem DB gleichen Namens **und** gleichem Membernamen könnte
        das theoretisch den falschen Kommentar liefern — in der Praxis
        (typischerweise ein PLC pro HMI-Verbindung) ist das vernachlässigbar.
        """
        return self._comments_by_db_member.get((db_name, member_path))

    def get_hmi_comment(self, hmi_name: str, table_name: str, tag_name: str) -> str | None:
        """Liefert den eigenen Kommentar eines HMI-Tags (WinCC Advanced/Comfort)
        aus der Kategorie ``<HMI comment>``, oder ``None`` falls keiner
        hinterlegt ist. Das ist der tatsächliche Kommentar der HMI-Variable
        selbst — nicht zu verwechseln mit dem "Quellkommentar" der
        verknüpften PLC-Variable (siehe ``get_by_db_member``).
        """
        return self._hmi_comments.get((hmi_name, table_name, tag_name))
