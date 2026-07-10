import logging
import sys
from pathlib import Path

from .exceptions import LoggerSetupError
from .schema import LoggingConfig

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(module)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(config: LoggingConfig) -> logging.Logger:
    """Configure the root logger and return it.

    Configures the root logger so all module-level loggers (logging.getLogger(__name__))
    automatically inherit the handlers and format.
    """
    root = logging.getLogger()

    # Clear existing handlers to avoid duplicates on repeated calls.
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)

    level = getattr(logging, config.level)
    root.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    if config.console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    if config.file:
        log_path = Path(config.file)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LoggerSetupError(
                f"Cannot create log directory '{log_path.parent}': {exc}"
            ) from exc
        try:
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
        except OSError as exc:
            raise LoggerSetupError(
                f"Cannot open log file '{log_path}': {exc}"
            ) from exc
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if not root.handlers:
        root.addHandler(logging.NullHandler())
        import warnings
        warnings.warn(
            "Logger configured with no output targets (console=False, file=None).",
            stacklevel=2,
        )

    return root
