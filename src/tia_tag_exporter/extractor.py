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

        WinCC Advanced/Comfort (``Siemens.Engineering.Hmi.HmiTarget``) und WinCC
        Unified (``Siemens.Engineering.HmiUnified.HmiSoftware``) haben in V21
        unterschiedliche Objektmodelle:

        - Advanced/Comfort: Tag-Tabellen hängen unter ``hmi.TagFolder`` (mit
          rekursiven Unterordnern über ``.Folders``), die Tag-Objekte
          (``Siemens.Engineering.Hmi.Tag.Tag``) besitzen kaum stark typisierte
          Properties — Datentyp/Verbindung/Kommentar müssen über
          ``GetAttribute`` gelesen werden.
        - Unified: ``hmi.TagTables`` liefert die Tag-Tabellen direkt als flache
          Liste, die Tag-Objekte (``HmiUnified.HmiTags.HmiTag``) haben echte
          Properties (``DataType``, ``Connection``, ``Comment``).

        Args:
            hmi: Ein HMI-Software-Objekt (Advanced/Comfort ``HmiTarget`` oder
                Unified ``HmiSoftware``).

        Returns:
            Liste von Dicts mit Name, Datentyp, Verbindung, Kommentar.
        """
        records: list[HmiTagRecord] = []
        tag_tables = list(self._iter_hmi_tag_tables(hmi))

        if not tag_tables:
            logger.warning("HMI-Objekt '{}' besitzt keine Tag-Tabellen", getattr(hmi, "Name", "?"))
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
                            "Kommentar": self._read_comment(self._get_value(tag, "Comment")),
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
            db: Ein ``PlcBlock``-Objekt vom Typ Datenbaustein
                (``Siemens.Engineering.SW.Blocks.DataBlock``, mit ``Interface``).

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
                        "Datentyp": self._get_value(member, "DataTypeName"),
                        "Offset": self._get_value(member, "Offset"),
                        "Kommentar": self._read_comment(self._get_value(member, "Comment")),
                        "Initialwert": self._get_value(member, "StartValue"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("DB-Variable '{}' konnte nicht gelesen werden: {}", full_name, exc)
                continue

            nested = self._get_nested_members(member)
            if nested is not None and len(nested) > 0:
                self._collect_members(nested, prefix=f"{full_name}.", records=records)

    @staticmethod
    def _get_nested_members(member: Any) -> Any:
        """Liest die Unter-Elemente eines Struct-Members.

        ``Member.Members`` ist keine normale .NET-Property, sondern eine
        explizite Interface-Implementierung (``IEngineeringObject.GetComposition``)
        — daher der Zugriff über ``GetComposition("Members")`` statt ``getattr``.
        """
        get_composition = getattr(member, "GetComposition", None)
        if get_composition is None:
            return None
        try:
            return get_composition("Members")
        except Exception:  # noqa: BLE001 — z. B. Members ohne Unterstruktur
            return None

    @staticmethod
    def _iter_tag_tables(tag_table_group: Any):
        """Iteriert rekursiv über alle PLC-Tag-Tabellen inkl. Unterordner."""
        for table in tag_table_group.TagTables:
            yield table
        for subgroup in getattr(tag_table_group, "Groups", []):
            yield from TagExtractor._iter_tag_tables(subgroup)

    @staticmethod
    def _iter_hmi_tag_tables(hmi: Any):
        """Iteriert über alle HMI-Tag-Tabellen, unabhängig vom HMI-Typ.

        Advanced/Comfort organisiert Tag-Tabellen rekursiv unter ``TagFolder``
        (``.TagTables`` + ``.Folders``); Unified stellt sie direkt und flach
        über ``TagTables`` am Software-Objekt bereit.
        """
        tag_folder = getattr(hmi, "TagFolder", None)
        if tag_folder is not None:
            yield from TagExtractor._iter_hmi_tag_folder(tag_folder)
            return

        for table in getattr(hmi, "TagTables", []) or []:
            yield table

    @staticmethod
    def _iter_hmi_tag_folder(tag_folder: Any):
        for table in getattr(tag_folder, "TagTables", []) or []:
            yield table
        for subfolder in getattr(tag_folder, "Folders", []) or []:
            yield from TagExtractor._iter_hmi_tag_folder(subfolder)

    @staticmethod
    def _get_value(obj: Any, name: str) -> Any:
        """Liest ein Attribut robust: zuerst als .NET-Property, sonst per ``GetAttribute``.

        Mehrere Openness-Objekttypen (z. B. DB-Interface-``Member`` oder
        WinCC-Advanced/Comfort-``Tag``) exposen Konfigurationswerte wie
        Datentyp, Offset oder Kommentar nicht als stark typisierte Properties,
        sondern nur über die generische ``GetAttribute``-Methode.
        """
        value = getattr(obj, name, None)
        if value is not None:
            return value

        get_attribute = getattr(obj, "GetAttribute", None)
        if get_attribute is None:
            return None
        try:
            return get_attribute(name)
        except Exception:  # noqa: BLE001 — Attribut existiert für dieses Objekt nicht
            return None

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

    @classmethod
    def _read_hmi_data_type(cls, tag: Any) -> str:
        return cls._get_value(tag, "DataType") or ""

    @classmethod
    def _read_hmi_connection(cls, tag: Any) -> str:
        connection = cls._get_value(tag, "Connection")
        if connection is None:
            return ""
        return getattr(connection, "Name", str(connection))
