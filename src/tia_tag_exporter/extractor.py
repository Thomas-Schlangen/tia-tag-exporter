"""Extraktion von PLC-Tags, HMI-Tags und DB-Variablen aus TIA-Portal-Objekten."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

PlcTagRecord = dict[str, Any]
HmiTagRecord = dict[str, Any]
DbVariableRecord = dict[str, Any]

# Elementare IEC-61131-Datentypen — Member mit einem dieser Typen (auch als
# "Array[...] of <Typ>") haben nie ein Struct/UDT dahinter und brauchen keine
# GetComposition("Members")-Sonde (siehe TagExtractor._is_elementary_type).
_ELEMENTARY_DATA_TYPES = frozenset(
    {
        "bool", "byte", "word", "dword", "lword",
        "sint", "usint", "int", "uint", "dint", "udint", "lint", "ulint",
        "real", "lreal",
        "char", "wchar", "string", "wstring",
        "time", "ltime", "date", "time_of_day", "tod", "ltime_of_day", "ltod",
        "date_and_time", "dtl", "s5time",
    }
)

# Attribute, die pro DB-Interface-Member benötigt werden. Statt vier einzelnen
# Member.GetAttribute(name)-Calls wird (falls von der jeweiligen TIA-Version
# unterstützt) ein einziger Member.GetAttributes([...])-Bulk-Call gemacht —
# siehe TagExtractor._read_member_attributes.
_WANTED_MEMBER_ATTRIBUTES = ("DataTypeName", "Offset", "Comment", "StartValue")


class TagExtractor:
    """Liest Tags und Variablen aus PLC-, HMI- und DB-Objekten der Openness API."""

    def __init__(self) -> None:
        # Pro TagExtractor-Instanz einmalig ermittelt (siehe
        # _read_member_attributes) und über alle DBs des Exports hinweg
        # wiederverwendet, da das Attribut-Schema am CLR-Typ hängt, nicht an
        # der konkreten Member-Instanz.
        self._member_attribute_names: list[str] | None = None

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
                        "PLC-Tag konnte nicht gelesen werden (Tabelle '%s'): %s",
                        getattr(table, "Name", "?"),
                        exc,
                    )

        logger.info("%d PLC-Tags extrahiert", len(records))
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
            logger.warning("HMI-Objekt '%s' besitzt keine Tag-Tabellen", getattr(hmi, "Name", "?"))
            return records

        for table in tag_tables:
            tags = getattr(table, "Tags", [])
            for tag in tags:
                try:
                    # Bei WinCC Advanced/Comfort (Siemens.Engineering.Hmi.Tag.Tag)
                    # exponiert Openness ausschließlich "Name" — Datentyp/Verbindung/
                    # Kommentar bleiben dort leer. Live verifiziert (siehe
                    # docs/setup-notes.md), kein Bug. Bei WinCC Unified sind es
                    # echte Properties und werden korrekt gefüllt.
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
                        "HMI-Tag konnte nicht gelesen werden (Tabelle '%s'): %s",
                        getattr(table, "Name", "?"),
                        exc,
                    )

        logger.info("%d HMI-Tags extrahiert", len(records))
        return records

    def extract_db_variables(self, db: Any, plc: Any) -> list[DbVariableRecord]:
        """Extrahiert alle Variablen aus einem Datenbaustein (rekursiv über Structs).

        Args:
            db: Ein ``PlcBlock``-Objekt vom Typ Datenbaustein
                (``Siemens.Engineering.SW.Blocks.DataBlock``, mit ``Interface``).
            plc: Das ``PlcSoftware``-Objekt, dem ``db`` angehört (für den
                Ordnerpfad, siehe ``_get_db_folder_path``).

        Returns:
            Liste von Dicts mit Name, Datentyp, Offset, Kommentar, Initialwert,
            sowie ``_folder_path`` (Ordnerpfad des DBs) und ``_db_name``.
        """
        db_name = getattr(db, "Name", "?")

        # TIA Portal V19 (headless/WithoutUserInterface) hat sich in Tests wiederholt
        # als instabil erwiesen: dieselbe Extraktion scheitert nicht-deterministisch
        # (mal sofort, mal gar nicht) mit einer EngineeringObjectDisposedException
        # ("TIA Portal has either been disposed or stopped running"), obwohl die
        # Session tatsächlich noch lebt — ein erneuter Versuch derselben Operation
        # gelingt in der Praxis meist. Reproduziert an zwei unabhängigen echten
        # V19-Projekten; bei V21 bisher nicht beobachtet. Deshalb hier ein kurzer
        # Retry statt den ganzen Export abzubrechen.
        max_attempts = 3
        records: list[DbVariableRecord] = []
        for attempt in range(1, max_attempts + 1):
            records = []
            try:
                members = db.Interface.Members
                self._collect_members(members, prefix="", records=records)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == max_attempts:
                    logger.warning(
                        "DB '%s': Openness-Zugriff nach %d Versuchen weiterhin "
                        "instabil, DB wird übersprungen: %s",
                        db_name,
                        max_attempts,
                        exc,
                    )
                    return []
                logger.warning(
                    "DB '%s': transiente Openness-Instabilität (Versuch %d/%d), "
                    "erneuter Versuch: %s",
                    db_name,
                    attempt,
                    max_attempts,
                    exc,
                )

        folder_path = self._get_db_folder_path(db, plc)
        for record in records:
            record["_folder_path"] = folder_path
            record["_db_name"] = db_name

        logger.info("%d DB-Variablen extrahiert aus '%s'", len(records), db_name)
        return records

    @classmethod
    def _get_db_folder_path(cls, db: Any, plc: Any) -> list[str]:
        """Ermittelt den Ordnerpfad eines DBs von der PLC-Wurzel bis zum direkten
        Elternordner (der DB selbst ist nicht enthalten), z. B.
        ``["PLC_1", "Programmbausteine", "Antriebe"]``.

        Läuft die ``Parent``-Kette der Baustein-Ordner (``PlcBlockGroup``/
        ``PlcBlockUserGroup``) hoch, bis sie beim Wurzel-``PlcBlockGroup``
        (``plc.BlockGroup``, per ``.Equals()`` erkannt) ankommt, und hängt
        davor ``plc.Name`` an.

        Live gegen ein reales Projekt verifiziert — zwei Annahmen der
        ursprünglichen Implementierung waren falsch, beide durch dasselbe
        pythonnet-Verhalten verursacht: ``db.Parent`` (und jeder weitere
        ``.Parent``) liefert Objekte, die pythonnet nur als generisches
        ``IEngineeringObject``-Interface typisiert — nicht als ihre konkrete
        Klasse. Dadurch (a) war ``getattr(node, "Name", None)`` immer
        ``None`` (die ``Name``-Property existiert auf dem konkreten Typ, ist
        über das Interface aber unsichtbar — Fix: ``GetAttribute("Name")``,
        das das Interface tatsächlich deklariert) und (b)
        ``isinstance(node, PlcSoftware)`` hat nie gematcht, wodurch die
        Schleife am ``PlcSoftware``-Knoten vorbei bis hoch zur
        ``TiaPortal``-Wurzel gelaufen wäre (Fix: ``node.Equals(plc.BlockGroup)``
        als Abbruchbedingung, da wir den Zielknoten bereits referenziell
        kennen, statt ihn per Typprüfung zu erkennen).
        """
        root_group = getattr(plc, "BlockGroup", None)
        segments: list[str] = []
        node = getattr(db, "Parent", None)

        depth = 0
        max_depth = 50  # Sicherheitsnetz falls root_group nie erreicht wird
        while node is not None and depth < max_depth:
            name = cls._get_value(node, "Name")
            if name:
                segments.append(name)
            if root_group is not None and node.Equals(root_group):
                break
            node = getattr(node, "Parent", None)
            depth += 1
        else:
            if depth >= max_depth:
                logger.warning(
                    "DB-Ordnerpfad: Wurzel-BlockGroup nach %d Ebenen nicht erreicht, "
                    "Pfad könnte unvollständig/zu lang sein.",
                    max_depth,
                )

        plc_name = cls._get_value(plc, "Name")
        if plc_name:
            segments.append(plc_name)

        segments.reverse()
        return segments

    def _collect_members(self, members: Any, prefix: str, records: list[DbVariableRecord]) -> None:
        for member in members:
            full_name = f"{prefix}{member.Name}"
            data_type = None
            try:
                # Offset/Comment bleiben leer, wenn der Baustein "Optimized" ist
                # (TIA-Standard seit vielen Versionen) — Openness kennt dafür keinen
                # festen Byte-Offset und keinen Member-Kommentar. Live gegen ein
                # reales Projekt verifiziert (siehe docs/setup-notes.md), kein Bug.
                values = self._read_member_attributes(member)
                data_type = values.get("DataTypeName")
                records.append(
                    {
                        "Name": full_name,
                        "Datentyp": data_type,
                        "Offset": values.get("Offset"),
                        "Kommentar": self._read_comment(values.get("Comment")),
                        "Initialwert": values.get("StartValue"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("DB-Variable '%s' konnte nicht gelesen werden: %s", full_name, exc)
                continue

            if self._is_elementary_type(data_type):
                continue

            nested = self._get_nested_members(member)
            if nested is not None and len(nested) > 0:
                self._collect_members(nested, prefix=f"{full_name}.", records=records)

    def _read_member_attributes(self, member: Any) -> dict[str, Any]:
        """Liest ``_WANTED_MEMBER_ATTRIBUTES`` für ein Member in möglichst
        wenigen Remote-Calls.

        Openness kennt neben ``member.GetAttribute(name)`` (ein Call pro
        Attribut) auch ``member.GetAttributes([...])`` (ein Bulk-Call für
        mehrere Attribute auf einmal) — allerdings wirft der Bulk-Call eine
        harte Exception, sobald auch nur ein angefragter Name für den
        konkreten Member-Typ nicht existiert (z. B. "Offset"/"Comment" gibt es
        bei V19 gar nicht als Attribut). Deshalb wird einmalig (nicht pro
        Member!) über ``GetAttributeInfos()`` ermittelt, welche der
        gewünschten Namen tatsächlich unterstützt werden, und danach immer
        nur mit dieser gefilterten Liste gebulkt.

        Der Unterschied ist bei Datenbausteinen mit tausenden Membern (z. B.
        große flache Arrays) massiv: 1 Bulk-Call statt 4 Einzel-Calls pro
        Member. Live gegen TIA Portal V19 verifiziert — ohne diese
        Bündelung bricht die Openness-Session bei DBs mit vielen Membern
        mitten in der Extraktion ab.
        """
        if self._member_attribute_names is None:
            self._member_attribute_names = self._detect_supported_attributes(member)

        if not self._member_attribute_names:
            return {name: self._get_value(member, name) for name in _WANTED_MEMBER_ATTRIBUTES}

        try:
            from System import String
            from System.Collections.Generic import List

            net_names = List[String]()
            for name in self._member_attribute_names:
                net_names.Add(name)
            raw_values = list(member.GetAttributes(net_names))
            return dict(zip(self._member_attribute_names, raw_values))
        except Exception:  # noqa: BLE001 — Schema-Abweichung bei diesem Member, Einzelabfrage als Fallback
            return {name: self._get_value(member, name) for name in _WANTED_MEMBER_ATTRIBUTES}

    @staticmethod
    def _detect_supported_attributes(member: Any) -> list[str]:
        """Ermittelt einmalig, welche von ``_WANTED_MEMBER_ATTRIBUTES`` diese
        TIA-Version für Interface-Member tatsächlich unterstützt."""
        try:
            infos = member.GetAttributeInfos()
            available = {info.Name for info in infos}
        except Exception:  # noqa: BLE001
            return []
        return [name for name in _WANTED_MEMBER_ATTRIBUTES if name in available]

    @staticmethod
    def _is_elementary_type(data_type_name: Any) -> bool:
        """Grobe Heuristik, ob ein Datentyp elementar ist (kein Struct/UDT).

        Vermeidet die teure ``GetComposition("Members")``-Sonde (siehe
        ``_get_nested_members``) für Member, die garantiert keine Unterstruktur
        haben. Ohne diese Abkürzung ruft ``_collect_members`` die Sonde für
        *jedes* Member auf — bei Datenbausteinen mit großen flachen Arrays
        (z. B. ``Array[0..1023] of Bool``, wo Openness jedes Element zusätzlich
        als eigenes Top-Level-Member auflistet) sind das mehrere Tausend
        Remote-Calls für einen einzigen DB. Live gegen TIA Portal V19
        verifiziert: Ohne diese Abkürzung bricht die Openness-Session mitten in
        der Extraktion ab (``EngineeringObjectDisposedException``), mit der
        Abkürzung läuft derselbe DB durch.
        """
        if not data_type_name:
            return False
        name = str(data_type_name).strip().lower()
        if name.startswith("array[") and " of " in name:
            name = name.split(" of ", 1)[1].strip()
        return name in _ELEMENTARY_DATA_TYPES

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
