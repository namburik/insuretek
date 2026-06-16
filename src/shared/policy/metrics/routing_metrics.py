import logging

logger = logging.getLogger(__name__)


def record_routing_confidence(agent_id: str, confidence: float,
                              routing_mode: str = "auto", session_id: str = "",
                              correlation_id: str = "") -> None:
    from shared.common.infra.audit_decorator import audit_inline
    audit_inline(
        policy_type="quality_metric",
        operation=f"routing_confidence:{agent_id}",
        allowed=confidence >= 0.35,
        reason=f"Routing confidence {confidence:.3f} (mode={routing_mode})",
        metadata={"metric_type": "routing_confidence", "agent_id": agent_id,
                  "confidence": round(confidence, 4), "routing_mode": routing_mode},
        session_id=session_id or None,
        correlation_id=correlation_id or None,
    )
    logger.info("routing_confidence agent=%s confidence=%.3f", agent_id, confidence)
