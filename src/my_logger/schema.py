from typing import Optional

from pydantic import BaseModel, field_validator

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: Optional[str] = None
    console: bool = True

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        upper = v.upper()
        if upper not in _VALID_LEVELS:
            raise ValueError(
                f"Invalid log level '{v}'. Must be one of: {', '.join(sorted(_VALID_LEVELS))}"
            )
        return upper
