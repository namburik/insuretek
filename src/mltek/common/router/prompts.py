"""Router/intent-classification prompts — loaded from domain configuration at runtime."""

# These are loaded from domain provider at initialization time
KEYWORD_ROUTES: list[tuple[str, str]] = []
INTENT_RESOLVER_SYSTEM: str = ""
INTENT_RESOLVER_PROMPT: str = ""  # Legacy alias


def init_routing_config() -> None:
    """Initialize routing configuration from domain provider.

    Call this once at startup after DomainPolicyProvider is registered.
    """
    import logging
    logger = logging.getLogger(__name__)

    global KEYWORD_ROUTES, INTENT_RESOLVER_SYSTEM, INTENT_RESOLVER_PROMPT
    try:
        from mltek.common.router.resolver import get_domain_provider
        provider = get_domain_provider()
        if not provider:
            logger.warning("Domain provider not available for routing config initialization")
            KEYWORD_ROUTES = []
            INTENT_RESOLVER_SYSTEM = ""
            return

        cfg = provider.get_routing_config()
        if not cfg:
            logger.warning("get_routing_config() returned None or empty dict")
            KEYWORD_ROUTES = []
            INTENT_RESOLVER_SYSTEM = ""
            return

        KEYWORD_ROUTES = cfg.get("keyword_routes", [])
        INTENT_RESOLVER_SYSTEM = cfg.get("intent_resolver_system", "")
        INTENT_RESOLVER_PROMPT = INTENT_RESOLVER_SYSTEM

        logger.info("Routing config initialized: %d keyword routes, system_prompt_len=%d",
                   len(KEYWORD_ROUTES), len(INTENT_RESOLVER_SYSTEM))
    except Exception as e:
        logger.error("Failed to initialize routing config: %s", e, exc_info=True)
        # Fallback: empty routes if provider not available
        KEYWORD_ROUTES = []
        INTENT_RESOLVER_SYSTEM = ""
