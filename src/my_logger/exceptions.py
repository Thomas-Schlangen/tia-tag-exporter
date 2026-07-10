class LoggerSetupError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(f"Failed to set up logger: {reason}")
