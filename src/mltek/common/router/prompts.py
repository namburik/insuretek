"""Router/intent-classification prompts — loaded from domain configuration at runtime."""

# These are loaded from domain provider at initialization time
KEYWORD_ROUTES: list[tuple[str, str]] = []
INTENT_RESOLVER_SYSTEM: str = ""
INTENT_RESOLVER_PROMPT: str = ""  # Legacy alias


def init_routing_config() -> None:
    """Initialize routing configuration from domain provider.

    Call this once at startup after DomainPolicyProvider is registered.
    """
    global KEYWORD_ROUTES, INTENT_RESOLVER_SYSTEM, INTENT_RESOLVER_PROMPT
    try:
        from mltek.common.router.resolver import get_domain_provider
        provider = get_domain_provider()
        if provider:
            cfg = provider.get_routing_config()
            KEYWORD_ROUTES = cfg.get("keyword_routes", [])
            INTENT_RESOLVER_SYSTEM = cfg.get("intent_resolver_system", "")
            INTENT_RESOLVER_PROMPT = INTENT_RESOLVER_SYSTEM
    except Exception:
        # Fallback: empty routes if provider not available
        KEYWORD_ROUTES = []
        INTENT_RESOLVER_SYSTEM = ""
