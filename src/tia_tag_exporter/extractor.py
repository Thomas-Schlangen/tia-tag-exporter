"""Extraktion von PLC-Tags, HMI-Tags und DB-Variablen aus TIA-Portal-Objekten."""

from __future__ import annotations

from typing import Any

from loguru import logger

PlcTagRecord = dict[str, Any]
HmiTagRecord = dict[str, Any]
DbVariableRecord = dict[str, Any]


class TagExtractor:
    """Liest Tags und Variablen aus PLC-, HMI- und DB-Objekten der Openness API."""

    def extract_plc_tags(self, plc: Any) -> list[PlcTagRecord]:
        """Extrahiert alle PLC-Tags aus allen Tag-Tabellen einer Steuerung.

        Args:
            plc: Ein ``PlcSoftware``-Objekt (aus ``device_item.GetService[SoftwareContainer]()``).

        Returns:
            Liste von Dicts mit Name, Datentyp, Adresse, Kommentar, Zugriffsebene.
        """
        records: list[PlcTagRecord] = []
        tag_table_group = plc.TagTableGroup

        for table in self._iter_tag_tables(tag_table_group):
            for tag in table.Tags:
                try:
                    records.append(
                        {
                            "Name": tag.Name,
                            "Datentyp": tag.DataTypeName,
                            "Adresse": tag.LogicalAddress,
                            "Kommentar": self._read_comment(tag.Comment),
                            "Zugriffsebene": self._read_access_level(tag),
                        }
                    )
                except Exception as exc:  # noqa: BLE001 — Openness/.NET-Fehler pro Tag abfangen
                    logger.warning(
                        "PLC-Tag konnte nicht gelesen werden (Tabelle '{}'): {}",
                        getattr(table, "Name", "?"),
                        exc,
                    )

        logger.info("{} PLC-Tags extrahiert", len(records))
        return records

    def extract_hmi_tags(self, hmi: Any) -> list[HmiTagRecord]:
        """Extrahiert alle HMI-Tags aus allen Tag-Tabellen eines HMI-Geräts.

        Unterstützt sowohl WinCC Advanced/Comfort (``HmiTarget``) als auch
        WinCC Unified (``HmiUnifiedTarget``) — beide stellen ``TagTables`` bereit,
        die Tag-Objekte unterscheiden sich aber in wenigen Attributnamen.

        Args:
            hmi: Ein HMI-Target-Objekt (Advanced/Comfort oder Unified).

        Returns:
            Liste von Dicts mit Name, Datentyp, Verbindung, Kommentar.
        """
        records: list[HmiTagRecord] = []
        tag_tables = getattr(hmi, "TagTables", None)

        if tag_tables is None:
            logger.warning("HMI-Objekt '{}' besitzt keine TagTables", getattr(hmi, "Name", "?"))
            return records

        for table in tag_tables:
            tags = getattr(table, "Tags", [])
            for tag in tags:
                try:
                    records.append(
                        {
                            "Name": tag.Name,
                            "Datentyp": self._read_hmi_data_type(tag),
                            "Verbindung": self._read_hmi_connection(tag),
                            "Kommentar": self._read_comment(getattr(tag, "Comment", None)),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "HMI-Tag konnte nicht gelesen werden (Tabelle '{}'): {}",
                        getattr(table, "Name", "?"),
                        exc,
                    )

        logger.info("{} HMI-Tags extrahiert", len(records))
        return records

    def extract_db_variables(self, db: Any) -> list[DbVariableRecord]:
        """Extrahiert alle Variablen aus einem Datenbaustein (rekursiv über Structs).

        Args:
            db: Ein ``PlcBlock``-Objekt vom Typ Datenbaustein (mit ``Interface``).

        Returns:
            Liste von Dicts mit Name, Datentyp, Offset, Kommentar, Initialwert.
        """
        records: list[DbVariableRecord] = []

        try:
            members = db.Interface.Members
        except Exception as exc:  # noqa: BLE001
            logger.warning("Interface von DB '{}' nicht lesbar: {}", getattr(db, "Name", "?"), exc)
            return records

        self._collect_members(members, prefix="", records=records)
        logger.info("{} DB-Variablen extrahiert aus '{}'", len(records), getattr(db, "Name", "?"))
        return records

    def _collect_members(self, members: Any, prefix: str, records: list[DbVariableRecord]) -> None:
        for member in members:
            full_name = f"{prefix}{member.Name}"
            try:
                records.append(
                    {
                        "Name": full_name,
                        "Datentyp": member.DataTypeName,
                        "Offset": getattr(member, "Offset", None),
                        "Kommentar": self._read_comment(getattr(member, "Comment", None)),
                        "Initialwert": getattr(member, "StartValue", None),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("DB-Variable '{}' konnte nicht gelesen werden: {}", full_name, exc)
                continue

            nested = getattr(member, "Members", None)
            if nested is not None and len(nested) > 0:
                self._collect_members(nested, prefix=f"{full_name}.", records=records)

    @staticmethod
    def _iter_tag_tables(tag_table_group: Any):
        """Iteriert rekursiv über alle Tag-Tabellen inkl. Unterordner."""
        for table in tag_table_group.TagTables:
            yield table
        for subgroup in getattr(tag_table_group, "Groups", []):
            yield from TagExtractor._iter_tag_tables(subgroup)

    @staticmethod
    def _read_comment(comment: Any) -> str:
        """Liest den Standard-Sprachtext aus einem MultilingualText-Objekt."""
        if comment is None:
            return ""
        items = getattr(comment, "Items", None)
        if items is None:
            return str(comment)
        for item in items:
            text = getattr(item, "Text", None)
            if text:
                return text
        return ""

    @staticmethod
    def _read_access_level(tag: Any) -> str:
        """Leitet eine lesbare Zugriffsebene aus den Openness-Flags eines PLC-Tags ab."""
        visible = getattr(tag, "ExternalVisible", True)
        writable = getattr(tag, "ExternalWritable", True)
        accessible = getattr(tag, "ExternalAccessible", True)

        if not accessible:
            return "gesperrt"
        if not visible:
            return "unsichtbar (HMI/OPC)"
        if not writable:
            return "nur lesend"
        return "voller Zugriff"

    @staticmethod
    def _read_hmi_data_type(tag: Any) -> str:
        return getattr(tag, "DataTypeName", None) or getattr(tag, "DataType", "")

    @staticmethod
    def _read_hmi_connection(tag: Any) -> str:
        connection = getattr(tag, "Connection", None)
        if connection is None:
            return ""
        return getattr(connection, "Name", str(connection))
