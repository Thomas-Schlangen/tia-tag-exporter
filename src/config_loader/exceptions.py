class ConfigFileNotFoundError(FileNotFoundError):
    def __init__(self, path: str):
        super().__init__(f"Config file not found: '{path}'")


class ConfigFormatError(ValueError):
    def __init__(self, path: str, reason: str):
        super().__init__(f"Invalid format in '{path}': {reason}")


class ConfigValidationError(ValueError):
    def __init__(self, path: str, errors: list):
        lines = "\n  ".join(
            f"{' -> '.join(str(loc) for loc in e['loc'])}: {e['msg']}"
            for e in errors
        )
        super().__init__(f"Validation failed for '{path}':\n  {lines}")
