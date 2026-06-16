"""Lightweight response evaluator for the agent pipeline."""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
import re
from shared.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


class QualityLevel(Enum):
    EXCELLENT = "excellent"  # 85%+
    GOOD = "good"            # 70-85%
    FAIR = "fair"            # 50-70%
    POOR = "poor"            # <50%


@dataclass
class QuickEvalResult:
    score: float  # 0.0 to 1.0
    quality_level: QualityLevel
    has_errors: bool
    has_keywords: bool
    is_substantive: bool
    details: str = ""

    def __str__(self) -> str:
        emoji = {
            QualityLevel.EXCELLENT: "✓",
            QualityLevel.GOOD: "✓",
            QualityLevel.FAIR: "⚠",
            QualityLevel.POOR: "✗",
        }.get(self.quality_level, "?")
        return f"{emoji} {self.quality_level.value.upper()} ({self.score:.0%})"


@dataclass
class EvalConfig:
    enable_eval: bool = True
    enable_logging: bool = True
    min_quality_threshold: float = 0.5
    retry_on_low_quality: bool = False
    max_retries: int = 3
    verbose: bool = False


class ResponseEvaluator:
    def __init__(self, config: Optional[EvalConfig] = None):
        self.config = config or EvalConfig()

    def quick_eval(
        self,
        response: str,
        operation_type: str = "query",
        expected_keywords: Optional[List[str]] = None,
        excluded_keywords: Optional[List[str]] = None,
    ) -> QuickEvalResult:
        if not self.config.enable_eval:
            return QuickEvalResult(
                score=1.0,
                quality_level=QualityLevel.EXCELLENT,
                has_errors=False,
                has_keywords=True,
                is_substantive=True,
                details="Evaluation disabled",
            )

        response_lower = response.lower()

        # Check 1: Error indicators
        error_keywords = ["error", "exception", "failed", "invalid", "traceback"]
        has_errors = any(err in response_lower for err in error_keywords)

        # Check 2: Expected keywords — partial credit proportional to fraction found
        expected_keywords = expected_keywords or []
        missing: List[str] = []
        if expected_keywords:
            missing = [kw for kw in expected_keywords if kw.lower() not in response_lower]
            keyword_fraction = (len(expected_keywords) - len(missing)) / len(expected_keywords)
        else:
            keyword_fraction = 1.0
        has_keywords = len(missing) == 0

        # Check 3: Excluded keywords — skip any that already appear in error_keywords to
        # avoid penalising the same word twice (once via has_errors, once here).
        excluded_keywords = excluded_keywords or []
        has_excluded = False
        if excluded_keywords:
            effective_excluded = [kw for kw in excluded_keywords if kw not in error_keywords]
            has_excluded = any(kw.lower() in response_lower for kw in effective_excluded)

        # Check 4: Substantive (not empty/truncated)
        is_substantive = len(response.strip()) >= 20

        # Score: no errors 25%, keyword coverage 0–35%, no excluded 20%, substantive 20%
        score = 0.0
        if not has_errors:
            score += 0.25
        score += keyword_fraction * 0.35
        if not has_excluded:
            score += 0.20
        if is_substantive:
            score += 0.20

        if score >= 0.85:
            quality = QualityLevel.EXCELLENT
        elif score >= 0.70:
            quality = QualityLevel.GOOD
        elif score >= 0.50:
            quality = QualityLevel.FAIR
        else:
            quality = QualityLevel.POOR

        details_parts = []
        if has_errors:
            details_parts.append(f"⚠ Found error keywords")
        if missing:
            details_parts.append(f"⚠ Missing keywords: {', '.join(missing)}")
        if has_excluded:
            details_parts.append(f"⚠ Found excluded keywords")
        if not is_substantive:
            details_parts.append(f"⚠ Response too short ({len(response)} chars)")
        details = "; ".join(details_parts) if details_parts else "✓ All checks passed"

        result = QuickEvalResult(
            score=score,
            quality_level=quality,
            has_errors=has_errors,
            has_keywords=has_keywords,
            is_substantive=is_substantive,
            details=details,
        )

        if self.config.enable_logging:
            logger.info(
                "Response evaluated: operation=%s quality=%s score=%.2f has_errors=%s has_keywords=%s is_substantive=%s details=%s",
                operation_type, result.quality_level, result.score,
                result.has_errors, result.has_keywords, result.is_substantive, result.details,
            )

        return result

    def should_retry(self, eval_result: QuickEvalResult, current_attempt: int = 1) -> bool:
        if not self.config.retry_on_low_quality:
            return False
        if eval_result.score >= self.config.min_quality_threshold:
            return False
        if current_attempt >= self.config.max_retries:
            return False
        return True

    def create_eval_badge(self, eval_result: QuickEvalResult) -> str:
        emoji = {
            QualityLevel.EXCELLENT: "✓",
            QualityLevel.GOOD: "✓",
            QualityLevel.FAIR: "⚠",
            QualityLevel.POOR: "✗",
        }[eval_result.quality_level]
        return f"{emoji} Quality: {eval_result.quality_level.value.upper()} ({eval_result.score:.0%})"


# ── Specialized evaluators ────────────────────────────────────────────────────

class ApplicantOperationEvaluator(ResponseEvaluator):
    def eval_applicant_query(self, response: str) -> QuickEvalResult:
        return self.quick_eval(response, "query", ["applicant", "id", "name"], ["error", "failed", "no applicants found"])

    def eval_applicant_create(self, response: str) -> QuickEvalResult:
        return self.quick_eval(response, "create", ["created", "applicant", "id"], ["error", "failed", "unable"])

    def eval_applicant_list(self, response: str) -> QuickEvalResult:
        return self.quick_eval(response, "list", ["applicant", "count", "total"], ["error", "failed"])


class GroupOperationEvaluator(ResponseEvaluator):
    def eval_group_query(self, response: str) -> QuickEvalResult:
        return self.quick_eval(response, "query", ["group", "account"], ["error", "failed"])

    def eval_group_create(self, response: str) -> QuickEvalResult:
        return self.quick_eval(response, "create", ["created", "group", "id"], ["error", "failed", "unable"])


# ── Pipeline helpers ──────────────────────────────────────────────────────────

def eval_step(answer: str, eval_type: Optional[str]) -> Optional[float]:
    """Returns score 0–1 for a workflow step, or None if eval_type is unset."""
    if not eval_type:
        return None
    try:
        return evaluate_agent_response(answer, eval_type).score
    except Exception:
        return None


def quality_label(avg_score: float) -> str:
    """Maps an average score to a quality band label."""
    if avg_score >= 0.85:
        return "EXCELLENT"
    if avg_score >= 0.70:
        return "GOOD"
    if avg_score >= 0.50:
        return "FAIR"
    return "POOR"


def evaluate_agent_response(
    response_text: str,
    operation_type: str = "query",
    config: Optional[EvalConfig] = None,
) -> QuickEvalResult:
    """Convenience wrapper: scores a response for a given operation type."""
    expected_keywords = {
        "query":  ["applicant", "group", "found", "result"],
        "create": ["created", "id", "success"],
        "update": ["updated", "modified", "success"],
        "list":   ["applicant", "group", "list", "count", "total"],
        "delete": ["deleted", "removed", "success"],
    }.get(operation_type, [])
    excluded_keywords = ["error", "failed", "exception", "unable to complete", "allowed steps"]
    return ResponseEvaluator(config=config).quick_eval(
        response=response_text,
        operation_type=operation_type,
        expected_keywords=expected_keywords,
        excluded_keywords=excluded_keywords,
    )
