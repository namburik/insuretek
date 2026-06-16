"""Repository classes for all domain entities."""

from core_data.repositories.applicant_repository import ApplicantRepository


def get_applicant_repository() -> ApplicantRepository:
    return ApplicantRepository()


__all__ = [
    "ApplicantRepository",
    "get_applicant_repository",
]
