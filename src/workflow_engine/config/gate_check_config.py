from typing import Any, Dict

from mltek.config.config_common import Config

_MOCK_BASE = Config.BFF_SERVICE_URL.rstrip("/")


def _url(prod_url: str, gate_check: str) -> str:
    """Return the mock URL when BFF_SERVICE_URL is set, else the real provider URL."""
    return f"{_MOCK_BASE}/mock/gate-check/{gate_check}" if _MOCK_BASE else prod_url


GATE_CHECK_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "credit_score":        {"url": _url("https://credit.bureau.internal/score",   "credit_score"),        "is_essential": True,  "timeout": 10.0},
    "loss_history":        {"url": _url("https://clue.internal/history",           "loss_history"),        "is_essential": True,  "timeout": 15.0},
    "sanctions_screening": {"url": _url("https://ofac.provider.internal/screen",  "sanctions_screening"), "is_essential": True,  "timeout": 8.0},
    "actuarial_review":    {"url": _url("https://uw.insurer.internal/review",      "actuarial_review"),    "is_essential": True,  "timeout": 30.0},
    "fraud":               {"url": _url("https://fraud.provider.internal/check",   "fraud"),               "is_essential": False, "timeout": 12.0},
}

ESSENTIAL_GATE_CHECKS: frozenset = frozenset(
    name for name, cfg in GATE_CHECK_ENDPOINTS.items() if cfg["is_essential"]
)
