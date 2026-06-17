"""Domain exceptions for the InsureTek application."""

from mltek.common.infra.exceptions import MltekException


class InsuretekException(MltekException):
    """Base exception for all InsureTek domain errors."""
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
