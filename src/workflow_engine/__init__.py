# Initialize domain provider when workflow_engine is imported
# This ensures KEYWORD_ROUTES and INTENT_RESOLVER_SYSTEM are loaded before any resolver calls
try:
    from mltek.common.router.resolver import register_domain_provider
    from insuretek.policy.domain_provider import InsureTekDomainProvider
    register_domain_provider(InsureTekDomainProvider())
except Exception:
    pass  # Domain provider may already be registered elsewhere
