"""Custom exceptions for Insuretek MCP System."""


class InsuretekException(Exception):
    """Base exception for all Insuretek errors."""
    pass


class ApplicantException(InsuretekException):
    """Base exception for applicant-related errors."""
    pass


class ApplicantNotFoundError(ApplicantException):
    """Raised when an applicant cannot be found."""
    def __init__(self, applicant_id: str):
        self.applicant_id = applicant_id
        super().__init__(f"Applicant not found: {applicant_id}")


class InvalidApplicantError(ApplicantException):
    """Raised when applicant data is invalid."""
    def __init__(self, message: str, field: str = None):
        self.field = field
        super().__init__(message)



class ConfigException(InsuretekException):
    """Raised when configuration is invalid or incomplete."""
    def __init__(self, message: str, missing_keys: list = None):
        self.missing_keys = missing_keys or []
        super().__init__(message)


class DatabaseException(InsuretekException):
    """Raised when database operation fails."""
    pass
