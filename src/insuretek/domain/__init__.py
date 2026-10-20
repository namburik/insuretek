"""Domain models and configuration for the Insuretek MCP system."""

from .enums import (
    PolicyType,
    RiskTier,
    PolicyStatus,
)
from .constants import (
    ServerDefaults,
    DynamoDBTableNames,
    ValidationMessages,
)
from .model_applicant import (
    ApplicantData,
    ApplicantUpdateData,
    PolicyData,
    CoverageRequest,
    RiskProfile,
    LocationAddress,
    ContactInfo,
)
from mltek.config.config_common import Config, config

__all__ = [
    # Enums
    "PolicyType",
    "RiskTier",
    "PolicyStatus",
    # Constants
    "ServerDefaults",
    "DynamoDBTableNames",
    "ValidationMessages",
    # Models
    "ApplicantData",
    "ApplicantUpdateData",
    "PolicyData",
    "CoverageRequest",
    "RiskProfile",
    "LocationAddress",
    "ContactInfo",
    # Config
    "Config",
    "config",
]
