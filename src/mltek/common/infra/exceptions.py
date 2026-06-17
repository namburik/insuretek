"""Generic framework exceptions for mltek."""


class MltekException(Exception):
    """Base exception for all mltek framework errors."""
    pass


class ConfigException(MltekException):
    """Raised when configuration is invalid or incomplete."""
    def __init__(self, message: str, missing_keys: list = None):
        self.missing_keys = missing_keys or []
        super().__init__(message)


class DatabaseException(MltekException):
    """Raised when a database operation fails."""
    pass
