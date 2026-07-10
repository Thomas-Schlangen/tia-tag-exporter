"""Extraktion von PLC-Tags, HMI-Tags und DB-Variablen aus TIA-Portal-Objekten."""

from __future__ import annotations

import logging
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tia_tag_exporter.project_texts import ProjectTextComments

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
        # Cache: exakte Menge der von einem Member unterstützten Attribute
        # (via GetAttributeInfos()) -> gefilterte Teilmenge von
        # _WANTED_MEMBER_ATTRIBUTES. Muss pro Member-Form (nicht global)
        # gecacht werden, siehe _read_member_attributes.
        self._member_attribute_cache: dict[frozenset[str], list[str]] = {}

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
            table_name = getattr(table, "Name", None) or "Default"
            for tag in table.Tags:
                try:
                    records.append(
                        {
                            "Variablentabelle": table_name,
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
                        table_name,
                        exc,
                    )

        logger.info("%d PLC-Tags extrahiert", len(records))
        return records

    def extract_hmi_tags(
        self, hmi: Any, project_texts: "ProjectTextComments | None" = None
    ) -> list[HmiTagRecord]:
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
            project_texts: Nachschlage-Tabelle für Kommentare (siehe
                ``project_texts.ProjectTextComments``) — wird für zwei Spalten
                gebraucht: ``Kommentar`` (eigener Tag-Kommentar, bei WinCC
                Advanced/Comfort über Openness nicht direkt abrufbar, siehe
                ``get_hmi_comment``) und ``Quellkommentar`` (Kommentar der
                verknüpften PLC-Variable, siehe ``_read_quellkommentar``).

        Returns:
            Liste von Dicts mit Name, Datentyp, Verbindung, PLC-Variable,
            Kommentar, Quellkommentar.
        """
        records: list[HmiTagRecord] = []
        tag_tables = list(self._iter_hmi_tag_tables(hmi))
        hmi_name = getattr(hmi, "Name", "?")
        hmi_device_name = self._get_hmi_device_name(hmi)

        if not tag_tables:
            logger.warning("HMI-Objekt '%s' besitzt keine Tag-Tabellen", hmi_name)
            return records

        for table in tag_tables:
            table_name = getattr(table, "Name", None) or "Default"
            controller_tags = self._read_controller_tags(table)
            tags = getattr(table, "Tags", [])
            for tag in tags:
                try:
                    # Bei WinCC Advanced/Comfort (Siemens.Engineering.Hmi.Tag.Tag)
                    # exponiert Openness selbst ausschließlich "Name" — Datentyp/
                    # Verbindung/Kommentar sind über GetAttribute/Property nicht
                    # abrufbar (live verifiziert, siehe docs/setup-notes.md). Der
                    # eigene Kommentar kommt daher aus der Projekttexte-Kategorie
                    # "<HMI comment>" (siehe ProjectTextComments.get_hmi_comment).
                    # Bei WinCC Unified ist "Comment" eine echte Property und wird
                    # direkt gelesen, ohne den Projekttexte-Umweg.
                    controller_tag = controller_tags.get(tag.Name)
                    comment = self._read_comment(self._get_value(tag, "Comment"))
                    if not comment and project_texts is not None and hmi_device_name is not None:
                        comment = project_texts.get_hmi_comment(hmi_device_name, table_name, tag.Name)
                    # Quellkommentar: NICHT der Kommentar des HMI-Tags selbst,
                    # sondern der Kommentar der verknüpften PLC-Variable
                    # (Quelle = PLC-Seite) — bewusst eine eigene Spalte, kein
                    # Fallback/Ersatz für "Kommentar".
                    quellkommentar = self._read_quellkommentar(controller_tag, project_texts)
                    records.append(
                        {
                            "Variablentabelle": table_name,
                            "Name": tag.Name,
                            "Datentyp": self._read_hmi_data_type(tag),
                            "Verbindung": self._read_hmi_connection(tag),
                            "PLC-Variable": controller_tag or "",
                            "Kommentar": comment or "",
                            "Quellkommentar": quellkommentar or "",
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "HMI-Tag konnte nicht gelesen werden (Tabelle '%s'): %s",
                        table_name,
                        exc,
                    )

        logger.info("%d HMI-Tags extrahiert", len(records))
        return records

    @classmethod
    def _get_hmi_device_name(cls, hmi: Any) -> str | None:
        """Ermittelt den Namen des Hardware-Geräts, dem ein HMI-Software-
        Container angehört (z. B. ``"pn4805-15A10"``) — NICHT ``hmi.Name``
        (das ist der Name des Software-Containers selbst, z. B.
        ``"HMI_RT_8"``, live verifiziert als unterschiedlich).

        Wird für die Projekttexte-Suche gebraucht: Der ``ViewPath`` der
        Kategorie ``<HMI comment>`` verwendet den Gerätenamen, nicht den
        Software-Container-Namen. Läuft die ``Parent``-Kette hoch (wie bei
        ``_get_db_folder_path`` liefert ``.Parent`` dabei generisch typisierte
        ``IEngineeringObject``-Objekte, daher ``_get_value`` statt ``getattr``
        für ``Name``) bis zur Projekt-Wurzel (deren ``Parent`` ``None`` ist)
        und nimmt den vorletzten Namen — direkt vor dem Projektnamen.
        """
        names: list[str] = []
        node = hmi
        depth = 0
        while node is not None and depth < 20:
            name = cls._get_value(node, "Name")
            if name:
                names.append(name)
            node = getattr(node, "Parent", None)
            depth += 1
        return names[-2] if len(names) >= 2 else None

    @classmethod
    def _read_quellkommentar(
        cls, controller_tag: str | None, project_texts: "ProjectTextComments | None"
    ) -> str | None:
        """Liefert den "Quellkommentar" eines HMI-Tags: den Kommentar der
        verknüpften PLC-Variable (die "Quelle" der HMI-Variable), NICHT den
        Kommentar des HMI-Tags selbst — das ist eine eigenständige Spalte
        ("Quellkommentar"), kein Ersatzwert für die Spalte "Kommentar".

        ``controller_tag`` im Format ``DB.Member[.Sub...]`` (siehe
        ``_read_controller_tags``), Lookup über dieselbe Projekttexte-
        Nachschlage-Tabelle wie die DB-Variablen-Kommentare. Ohne PLC-Namen im Schlüssel
        (siehe ``ProjectTextComments.get_by_db_member``), da für ein HMI-Tag
        an dieser Stelle nicht bekannt ist, zu welcher PLC die Ziel-DB gehört.
        """
        if not controller_tag or project_texts is None:
            return None
        parts = controller_tag.split(".", 1)
        if len(parts) != 2:
            return None
        db_name, member_path = parts
        return project_texts.get_by_db_member(
            cls._normalize_member_path(db_name), cls._normalize_member_path(member_path)
        )

    @staticmethod
    def _read_controller_tags(table: Any) -> dict[str, str]:
        """Ermittelt, mit welcher PLC-Variable jedes Tag einer HMI-Tag-Tabelle
        verknüpft ist (``{Tag-Name: PLC-Variable}``).

        Die Verknüpfung ist über das Openness-Objektmodell selbst nicht
        zugänglich — weder ``Siemens.Engineering.Hmi.Tag.Tag`` noch die
        generische ``GetAttribute``-Schnittstelle kennen ein "Connection"-
        oder "Address"-Attribut dafür (live erschöpfend verifiziert). Sie
        taucht aber im XML von ``HmiTagTable.Export()`` auf: jedes Tag-Element
        enthält dort einen ``<LinkList><ControllerTag><Name>`` -Verweis auf
        die gebundene PLC-Variable (fehlt komplett bei rein internen,
        nicht mit der PLC verknüpften Tags). Export erfolgt einmal pro
        Tabelle (nicht pro Tag) und wird in einem Temp-Verzeichnis
        zwischengelagert.

        Schlägt der Export fehl (z. B. weil ein Tabellentyp ``Export()`` nicht
        unterstützt), wird ein leeres Dict zurückgegeben — "PLC-Variable"
        bleibt dann für diese Tabelle leer, der restliche Export läuft
        unbeeinträchtigt weiter.
        """
        try:
            from System.IO import FileInfo
            from Siemens.Engineering import ExportOptions

            with tempfile.TemporaryDirectory() as tmp_dir:
                export_path = Path(tmp_dir) / "hmi_table.xml"
                table.Export(FileInfo(str(export_path)), ExportOptions.WithDefaults)

                result: dict[str, str] = {}
                root = ET.parse(export_path).getroot()
                for elem in root.iter():
                    attribute_list = elem.find("AttributeList")
                    link_list = elem.find("LinkList")
                    if attribute_list is None or link_list is None:
                        continue
                    name_elem = attribute_list.find("Name")
                    controller_tag_elem = link_list.find("ControllerTag")
                    if name_elem is None or controller_tag_elem is None:
                        continue
                    controller_name_elem = controller_tag_elem.find("Name")
                    if controller_name_elem is None or not controller_name_elem.text:
                        continue
                    result[name_elem.text] = controller_name_elem.text
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PLC-Verknüpfung für Tag-Tabelle '%s' konnte nicht gelesen werden: %s",
                getattr(table, "Name", "?"),
                exc,
            )
            return {}

    def extract_db_variables(
        self, db: Any, plc: Any, project_texts: "ProjectTextComments | None" = None
    ) -> list[DbVariableRecord]:
        """Extrahiert alle Variablen aus einem Datenbaustein (rekursiv über Structs).

        Args:
            db: Ein ``PlcBlock``-Objekt vom Typ Datenbaustein
                (``Siemens.Engineering.SW.Blocks.DataBlock``, mit ``Interface``).
            plc: Das ``PlcSoftware``-Objekt, dem ``db`` angehört (für den
                Ordnerpfad, siehe ``_get_db_folder_path``).
            project_texts: Nachschlage-Tabelle für DB-Variablen-Kommentare (siehe
                ``project_texts.ProjectTextComments``) — ``Interface.Member`` hat
                selbst kein Comment-Attribut, die Kommentare kommen aus der
                zentralen Projekttexte-Verwaltung. ``None`` lässt "Kommentar" leer.

        Returns:
            Liste von Dicts mit Name, Datentyp, Offset, Kommentar, Initialwert,
            sowie ``_folder_path`` (Ordnerpfad des DBs) und ``_db_name``.
        """
        db_name = getattr(db, "Name", "?")
        plc_name = getattr(plc, "Name", "?")

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
                self._collect_members(
                    members,
                    prefix="",
                    records=records,
                    plc_name=plc_name,
                    db_name=db_name,
                    project_texts=project_texts,
                )
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

    def _collect_members(
        self,
        members: Any,
        prefix: str,
        records: list[DbVariableRecord],
        plc_name: str,
        db_name: str,
        project_texts: "ProjectTextComments | None",
    ) -> None:
        for member in members:
            full_name = f"{prefix}{member.Name}"
            data_type = None
            try:
                # Offset bleibt leer, wenn der Baustein "Optimized" ist (TIA-Standard
                # seit vielen Versionen) — Openness kennt dafür keinen festen
                # Byte-Offset. Live gegen ein reales Projekt verifiziert (siehe
                # docs/setup-notes.md), kein Bug. "Comment" existiert auf
                # Interface.Member grundsätzlich nicht (ebenfalls live verifiziert,
                # über alle Member-Typen eines Projekts) — der Kommentar kommt
                # stattdessen aus der zentralen Projekttexte-Verwaltung, siehe
                # project_texts.ProjectTextComments.
                values = self._read_member_attributes(member)
                data_type = values.get("DataTypeName")
                comment = (
                    project_texts.get(
                        self._normalize_member_path(plc_name),
                        self._normalize_member_path(db_name),
                        self._normalize_member_path(full_name),
                    )
                    if project_texts is not None
                    else None
                )
                records.append(
                    {
                        "Name": full_name,
                        "Datentyp": data_type,
                        "Offset": values.get("Offset"),
                        "Kommentar": comment or "",
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
                self._collect_members(
                    nested,
                    prefix=f"{full_name}.",
                    records=records,
                    plc_name=plc_name,
                    db_name=db_name,
                    project_texts=project_texts,
                )

    @staticmethod
    def _normalize_member_path(full_name: str) -> str:
        """Entfernt Anführungszeichen, die TIA um Member-Namen setzt, die keine
        gültigen "einfachen" Bezeichner sind (z. B. weil sie mit einer Ziffer
        beginnen: ``member.Name`` liefert ``"4805_30M1"`` mit Quotes). Die
        ``ViewPath``-Segmente aus ``ExportProjectTexts()`` sind dagegen immer
        unquotiert — ohne diese Normalisierung matchen quotierte Membernamen
        nie einen Kommentar aus den Projekttexten. Live verifiziert: ohne
        diesen Fix wurden z. B. bei DB "Mp01" nur die wenigen unquotierten
        Membernamen (wie "lr") gefunden, alle quotierten (wie "4805_30M1")
        blieben ohne Kommentar, obwohl einer hinterlegt war.
        """
        return ".".join(segment.strip('"') for segment in full_name.split("."))

    def _read_member_attributes(self, member: Any) -> dict[str, Any]:
        """Liest ``_WANTED_MEMBER_ATTRIBUTES`` für ein Member in möglichst
        wenigen Remote-Calls.

        Openness kennt neben ``member.GetAttribute(name)`` (ein Call pro
        Attribut) auch ``member.GetAttributes([...])`` (ein Bulk-Call für
        mehrere Attribute auf einmal) — allerdings wirft der Bulk-Call eine
        harte Exception, sobald auch nur ein angefragter Name für den
        konkreten Member-Typ nicht existiert. Welche Attribute existieren,
        hängt vom *Typ* des Members ab, nicht nur von der TIA-Version: ein
        Struct-Container-Member (z. B. ein Bool-Array oder DB-Auto-Diagnose-
        Member) hat kein ``StartValue``, ein einfaches Skalar-Member schon.
        Eine frühere Version dieser Methode hat die unterstützten Attribute
        einmalig anhand des *ersten* Members im gesamten Export ermittelt und
        für den Rest wiederverwendet — war der erste Member zufällig ein
        Struct-Container ohne ``StartValue``, blieb "Initialwert" für den
        kompletten restlichen Export leer, obwohl es bei den meisten Membern
        eigentlich verfügbar gewesen wäre (live so aufgetreten und behoben).

        Deshalb wird ``GetAttributeInfos()`` für **jedes** Member neu
        aufgerufen (das lässt sich nicht sicher vermeiden, ohne wieder falsche
        Werte zu riskieren), das Ergebnis aber pro exakter Attribut-Menge
        gecacht, damit die Python-seitige Filterung nicht wiederholt werden
        muss. Macht 2 Remote-Calls pro Member (``GetAttributeInfos`` +
        ``GetAttributes``) statt der ursprünglichen 4 einzelnen
        ``GetAttribute``-Calls — immer noch eine spürbare Reduktion bei DBs
        mit tausenden Membern, aber korrekt für jede Member-Form.
        """
        try:
            infos = member.GetAttributeInfos()
            available = frozenset(info.Name for info in infos)
        except Exception:  # noqa: BLE001
            return {name: self._get_value(member, name) for name in _WANTED_MEMBER_ATTRIBUTES}

        wanted_names = self._member_attribute_cache.get(available)
        if wanted_names is None:
            wanted_names = [name for name in _WANTED_MEMBER_ATTRIBUTES if name in available]
            self._member_attribute_cache[available] = wanted_names

        if not wanted_names:
            return {}

        try:
            from System import String
            from System.Collections.Generic import List

            net_names = List[String]()
            for name in wanted_names:
                net_names.Add(name)
            raw_values = list(member.GetAttributes(net_names))
            return dict(zip(wanted_names, raw_values))
        except Exception:  # noqa: BLE001 — Schema-Abweichung bei diesem Member, Einzelabfrage als Fallback
            return {name: self._get_value(member, name) for name in _WANTED_MEMBER_ATTRIBUTES}

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
