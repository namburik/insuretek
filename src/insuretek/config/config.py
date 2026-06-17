"""Insuretek-specific configuration — domain variables only."""

import os


class InsureTekConfig:
    """InsureTek domain-specific configuration."""

    # Neo4j database name (domain-specific, defaults to insuretek)
    NEO4J_DATABASE: str = os.getenv("NEO4J_DATABASE", "insuretek")

    # Workflow API endpoint used by intake and gate-check callbacks (domain-specific)
    BFF_SERVICE_URL: str = os.getenv("BFF_SERVICE_URL", "http://127.0.0.1:6001")

    # OAuth / Auth provider configuration (domain-specific integrations)
    GITHUB_CLIENT_ID: str = os.getenv("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET: str = os.getenv("GITHUB_CLIENT_SECRET", "")
    LINKEDIN_CLIENT_ID: str = os.getenv("LINKEDIN_CLIENT_ID", "")
    LINKEDIN_CLIENT_SECRET: str = os.getenv("LINKEDIN_CLIENT_SECRET", "")
