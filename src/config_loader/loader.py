import json
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from .exceptions import ConfigFileNotFoundError, ConfigFormatError, ConfigValidationError

T = TypeVar("T", bound=BaseModel)

_SUPPORTED_EXTENSIONS = {".yaml", ".yml", ".json"}


def load_config(path: str | Path, schema: Type[T]) -> T:
    """Load a YAML or JSON config file and validate it against a Pydantic schema.

    Args:
        path: Path to the config file (.yaml, .yml, or .json).
        schema: A Pydantic BaseModel subclass defining the expected structure.

    Returns:
        A validated instance of `schema`.

    Raises:
        ConfigFileNotFoundError: If the file does not exist.
        ConfigFormatError: If the file cannot be parsed (wrong syntax or unsupported extension).
        ConfigValidationError: If the parsed data does not match the schema.
    """
    path = Path(path)

    if not path.exists():
        raise ConfigFileNotFoundError(str(path))

    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED_EXTENSIONS:
        raise ConfigFormatError(
            str(path),
            f"unsupported extension '{suffix}'. Supported: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}",
        )

    raw = path.read_text(encoding="utf-8")
    data = _parse(path, raw, suffix)

    if not isinstance(data, dict):
        raise ConfigFormatError(str(path), "top-level value must be a mapping/object")

    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise ConfigValidationError(str(path), exc.errors()) from exc


def _parse(path: Path, raw: str, suffix: str) -> object:
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML support. Install it with: pip install pyyaml"
            ) from exc
        try:
            return yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ConfigFormatError(str(path), str(exc)) from exc
    else:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigFormatError(str(path), str(exc)) from exc
