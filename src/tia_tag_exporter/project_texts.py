"""Liest DB-Variablen-Kommentare aus der zentralen TIA-Portal-Projekttexte-Verwaltung.

``Siemens.Engineering.SW.Blocks.Interface.Member`` (DB-Variablen) hat über
Openness kein ``Comment``-Attribut — live erschöpfend verifiziert über alle
Member-Typen eines realen Projekts (siehe docs/setup-notes.md). Die Kommentare
existieren aber sehr wohl im Projekt: TIA Portal verwaltet sie zentral unter
"Sprachen & Ressourcen > Projekttexte", exportierbar über
``Project.ExportProjectTexts()``. Dieses Modul exportiert die Projekttexte
einmalig in eine temporäre Excel-Datei und baut daraus eine Nachschlage-Tabelle
(PLC-Name, DB-Name, Variablenname) -> Kommentartext.

Live verifiziert: Zeilen der Kategorie ``<BlockCommentCategoryData>`` mit
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
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import openpyxl

logger = logging.getLogger(__name__)

_BLOCK_COMMENT_CATEGORY = "<BlockCommentCategoryData>"


class ProjectTextComments:
    """Nachschlage-Tabelle für DB-Variablen-Kommentare aus den Projekttexten."""

    def __init__(self) -> None:
        self._comments: dict[tuple[str, str, str], str] = {}
        self._comments_by_db_member: dict[tuple[str, str], str] = {}

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
                    if row[category_idx] != _BLOCK_COMMENT_CATEGORY:
                        continue
                    view_path = row[view_path_idx]
                    if not view_path:
                        continue
                    text = next((row[i] for i in lang_col_indices if row[i]), None)
                    if not text:
                        continue

                    segments = view_path.split("\\")
                    if len(segments) < 3:
                        continue
                    plc_name = segments[1]
                    db_name = segments[-2]
                    member_path = segments[-1]
                    self._comments[(plc_name, db_name, member_path)] = text
                    self._comments_by_db_member[(db_name, member_path)] = text
            finally:
                workbook.close()

        logger.info("%d DB-Variablen-Kommentare aus Projekttexten geladen", len(self._comments))

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
