import uuid
from pydantic import Field, field_validator
from typing import Any, Dict, Optional

from core_data.db.models.models_base import DatabaseModel, current_timestamp_ms


class PolicyAuditRecord(DatabaseModel):
    """Append-only audit record for a single policy decision."""

    audit_id: str = Field(..., description="Unique audit record identifier")
    session_id: Optional[str] = Field(default=None, description="JIT session ID")
    correlation_id: Optional[str] = Field(default=None, description="HTTP request correlation ID")
    request_id: Optional[str] = Field(default=None, description="Workflow request ID (set after submission)")
    policy_type: str = Field(..., description="Policy class: jit_elevation, routing, onboarding, servicing, guardian, llm_judge, reflection, tool_intent")
    operation: str = Field(default="", description="Tool name, agent name, or source channel")
    allowed: bool = Field(..., description="Whether the policy granted or denied")
    reason: str = Field(default="", description="Human-readable decision reason")
    env: str = Field(default="prod", description="Active environment: prod or shadow")
    phase: str = Field(default="collecting", description="JIT session phase at decision time")
    created_at: int = Field(default_factory=current_timestamp_ms, description="Decision timestamp (milliseconds UTC)", ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Policy-specific context (risk_level, confidence, score, etc.)")
    entity_id: Optional[str] = Field(default=None, description="Primary entity affected (applicant_id)")
    entity_type: Optional[str] = Field(default=None, description="Entity type: applicant,  workflow")

    @field_validator("session_id", "correlation_id", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v):
        return v or None

    @classmethod
    def create(
        cls,
        policy_type: str,
        allowed: bool,
        reason: str,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        operation: str = "",
        env: str = "prod",
        phase: str = "collecting",
        metadata: Optional[Dict[str, Any]] = None,
        entity_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        created_at: Optional[int] = None,
    ) -> "PolicyAuditRecord":
        return cls(
            audit_id=f"audit_{uuid.uuid4().hex}",
            session_id=session_id or None,
            correlation_id=correlation_id or None,
            request_id=request_id or None,
            policy_type=policy_type,
            operation=operation,
            allowed=allowed,
            reason=reason,
            env=env,
            phase=phase,
            metadata=metadata or {},
            entity_id=entity_id or None,
            entity_type=entity_type or None,
            **({"created_at": created_at} if created_at is not None else {}),
        )
