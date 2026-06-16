"""Centralized enums for applicant models."""

from enum import Enum

class PolicyType(str, Enum):
    HOME       = "HOME"
    AUTO       = "AUTO"
    COMMERCIAL = "COMMERCIAL"
    LIFE       = "LIFE"

class RiskTier(str, Enum):
    STANDARD   = "STANDARD"
    PREFERRED  = "PREFERRED"
    HIGH_RISK  = "HIGH_RISK"
    DECLINED   = "DECLINED"

class PolicyStatus(str, Enum):
    DRAFT      = "DRAFT"
    QUOTED     = "QUOTED"
    BOUND      = "BOUND"
    ACTIVE     = "ACTIVE"
    CANCELLED  = "CANCELLED"
    EXPIRED    = "EXPIRED"
