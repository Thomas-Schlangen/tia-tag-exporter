"""Pydantic-Schema für die YAML-Konfiguration des TIA Tag Exporters."""

from __future__ import annotations

from pydantic import BaseModel

from my_logger import LoggingConfig


class VersionConfig(BaseModel):
    dll_path: str


class TiaConfig(BaseModel):
    version: str
    versions: dict[str, VersionConfig]


class ExportConfig(BaseModel):
    output_dir: str
    include_plc_tags: bool = True
    include_hmi_tags: bool = True
    include_db_variables: bool = True


class AppConfig(BaseModel):
    tia: TiaConfig
    export: ExportConfig
    logging: LoggingConfig
