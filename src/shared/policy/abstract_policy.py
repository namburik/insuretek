from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class PolicyDecision(BaseModel):
    """Common result model for policy decisions."""
    allowed: bool
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class Policy(ABC):
    """Base contract for all policy implementations."""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    async def evaluate(self, *args: Any, **kwargs: Any) -> PolicyDecision:
        ...
