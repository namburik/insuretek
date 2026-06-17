"""system configuration and environment loading."""
import os
from pathlib import Path
from typing import Optional

import logging

logger = logging.getLogger(__name__)

# Optional .env loading from canonical and project root locations
try:
    from dotenv import load_dotenv
except Exception:
    def load_dotenv(path=None):
        return False

_project_root = Path(__file__).resolve().parents[3]
_env_paths = [
    # Preferred location for shared config
    _project_root / "src" / "shared" / "config" / ".env",
    _project_root / ".env",
]

for _env_file in _env_paths:
    if _env_file.exists():
        try:
            load_dotenv(str(_env_file))
            break
        except Exception:
            pass

os.environ["OTEL_SDK_DISABLED"] = "true"

class Config:
    """system configuration"""

    # Alternate PostgreSQL Configuration (for MLTEK or other DB)
    PG_HOST: str = os.getenv("PG_HOST", "localhost")
    PG_PORT: int = int(os.getenv("PG_PORT", "5432"))
    PG_DATABASE: str = os.getenv("PG_DATABASE", "mltek")
    PG_USER: str = os.getenv("PG_USER", "namburi")
    PG_PASSWORD: str = os.getenv("PG_PASSWORD", "namburi")
    
    # Database Configuration (control which DB to use)
    DB_TYPE: str = os.getenv("DB_TYPE", "dynamodb").lower()
    
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    # DynamoDB Configuration
    DYNAMODB_ENDPOINT: str | None = os.getenv("DYNAMODB_ENDPOINT")
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-1")
    AWS_ACCESS_KEY_ID: str | None = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: str | None = os.getenv("AWS_SECRET_ACCESS_KEY")
    
    # PostgreSQL Configuration (now using PG_* variables)
    POSTGRES_HOST: str = os.getenv("PG_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("PG_PORT", "5432"))
    POSTGRES_DB: str = os.getenv("PG_DATABASE", "mltek")
    POSTGRES_USER: str = os.getenv("PG_USER", "namburi")
    POSTGRES_PASSWORD: str = os.getenv("PG_PASSWORD", "namburi")
    
    # Neo4j Configuration (for hierarchy analytics and relationship queries)
    # pgvector — persistent merchant embeddings for AI Linker hybrid search
    PGVECTOR_ENABLED: bool = os.getenv("PGVECTOR_ENABLED", "false").lower() == "true"
    PGVECTOR_EMBEDDING_DIM: int = int(os.getenv(
        "PGVECTOR_EMBEDDING_DIM",
        "1024" if os.getenv("MODEL_PROVIDER", "ollama").lower() == "bedrock" else "768",
    ))

    NEO4J_ENABLED: bool = os.getenv("NEO4J_ENABLED", "false").lower() == "true"
    NEO4J_URI: str | None = os.getenv("NEO4J_URI")
    NEO4J_HOST: str = os.getenv("NEO4J_HOST", "localhost")
    NEO4J_PORT: int = int(os.getenv("NEO4J_PORT", "7687"))
    NEO4J_USER: str = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "neo4jneo4j")
    NEO4J_DATABASE: str = os.getenv("NEO4J_DATABASE", "mltek")
    
    # Workflow API endpoint used by intake and gate-check callbacks.
    BFF_SERVICE_URL: str = os.getenv("BFF_SERVICE_URL", "http://127.0.0.1:6001")

    # Guardian configuration
    GUARDIAN_ENABLED: bool = os.getenv("GUARDIAN_ENABLED", "false").lower() == "true"

    # Reflection policy — AutoGen multi-agent self-correction for Guardian blockings
    REFLECTION_ENABLED: bool = os.getenv("REFLECTION_ENABLED", "false").lower() == "true"

    # OrchestratorGraph — LangGraph-based orchestrator with checkpointing and HITL
    USE_ORCHESTRATOR_GRAPH: bool = os.getenv("USE_ORCHESTRATOR_GRAPH", "true").lower() == "true"

    # OAuth / Auth provider configuration
    GITHUB_CLIENT_ID: str = os.getenv("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET: str = os.getenv("GITHUB_CLIENT_SECRET", "")
    LINKEDIN_CLIENT_ID: str = os.getenv("LINKEDIN_CLIENT_ID", "")
    LINKEDIN_CLIENT_SECRET: str = os.getenv("LINKEDIN_CLIENT_SECRET", "")

    # MCP Server Configuration
    SERVER_HOST: str = os.getenv("SERVER_HOST", "127.0.0.1")

    MCP_CORE_PORT: int = int(os.getenv("MCP_CORE_PORT", "8091"))
    MCP_WORKFLOW_PORT: int = int(os.getenv("MCP_WORKFLOW_PORT", "8092"))
    MCP_SEMANTIC_PORT: int = int(os.getenv("MCP_SEMANTIC_PORT", "8093"))

    
    MCP_CORE_URL: str = os.getenv(
        "MCP_CORE_URL",
        f"http://{SERVER_HOST}:{MCP_CORE_PORT}/mcp_core"
    )

    MCP_SEMANTIC_URL: str = os.getenv(
        "MCP_SEMANTIC_URL",
        f"http://{SERVER_HOST}:{MCP_SEMANTIC_PORT}/mcp_semantic"
    )

    MCP_WORKFLOW_URL: str = os.getenv(
        "MCP_WORKFLOW_URL",
        f"http://{SERVER_HOST}:{MCP_WORKFLOW_PORT}/mcp_workflow"
    )

    MODEL_PROVIDER: str = os.getenv("MODEL_PROVIDER", "ollama").lower()

    # Google Gemini configuration 
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    GOOGLE_LLM_MODEL: str = os.getenv("GOOGLE_LLM_MODEL", "gemini-2.0-flash")
    GOOGLE_RESOLVER_LLM_MODEL: str = os.getenv("GOOGLE_RESOLVER_LLM_MODEL", "gemini-2.5-flash")
    GOOGLE_CHAT_LLM_MODEL: str = os.getenv("GOOGLE_CHAT_LLM_MODEL", "gemini-3.1-flash-lite")

    # LLM Configuration
    # Default to a more recent Qwen model; override via .env or environment
    LLM_MODEL: str = os.getenv("LLM_MODEL", "qwen3.5:2b")

    # Embedding model for semantic routing (no LLM call at routing time).
    # Ollama: set EMBEDDING_MODEL (default: nomic-embed-text)
    # Google: set GOOGLE_EMBEDDING_MODEL (default: models/text-embedding-004)
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "nomic-embed-text:latest")
    GOOGLE_EMBEDDING_MODEL: str = os.getenv("GOOGLE_EMBEDDING_MODEL", "models/text-embedding-004")

    # AWS Bedrock configuration
    BEDROCK_AWS_ACCESS_KEY_ID: str | None = os.getenv("BEDROCK_AWS_ACCESS_KEY_ID")
    BEDROCK_AWS_SECRET_ACCESS_KEY: str | None = os.getenv("BEDROCK_AWS_SECRET_ACCESS_KEY")
    BEDROCK_LLM_MODEL: str = os.getenv("BEDROCK_LLM_MODEL", "amazon.nova-pro-v1:0")
    BEDROCK_RESOLVER_LLM_MODEL: str = os.getenv("BEDROCK_RESOLVER_LLM_MODEL", "amazon.nova-2-lite-v1:0")
    BEDROCK_CHAT_LLM_MODEL: str = os.getenv("BEDROCK_CHAT_LLM_MODEL", "amazon.nova-2-lite-v1:0")
    BEDROCK_EMBEDDING_MODEL: str = os.getenv("BEDROCK_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")

    CHAT_LLM_MODEL: str = os.getenv("CHAT_LLM_MODEL", os.getenv("LLM_MODEL", "qwen3.5:2b"))
    RESOLVER_LLM_MODEL: str = os.getenv("RESOLVER_LLM_MODEL", os.getenv("CHAT_LLM_MODEL", os.getenv("LLM_MODEL", "qwen3.5:2b")))

    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0"))
    RESOLVER_TIMEOUT_SECONDS: int = int(os.getenv("RESOLVER_TIMEOUT_SECONDS", "360"))
    
    # Health Check Configuration
    HEALTH_CHECK_ON_AGENT_START: bool = os.getenv("HEALTH_CHECK_ON_AGENT_START", "true").lower() == "true"
    
    @classmethod
    def get_mcp_client_config(cls, env: str = "prod") -> dict:
        """Get MCP client configuration. Passes X-Env and X-Correlation-ID headers
        so the MCP server can route writes correctly and maintain trace continuity."""
        from mltek.common.infra.context import CorrelationContext
        headers = {"X-Env": env}
        correlation_id = CorrelationContext.get()
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        return {
            "http_tools_server": {
                "url": cls.MCP_CORE_URL,
                "transport": "streamable_http",
                "headers": headers,
            },
            "http_agent_skills_server": {
                "url": cls.MCP_SEMANTIC_URL,
                "transport": "streamable_http",
                "headers": headers,
            },
            "http_workflow_server": {
                "url": cls.MCP_WORKFLOW_URL,
                "transport": "streamable_http",
                "headers": headers,
            },
        }

    @classmethod
    def get_mcp_semantic_client_config(cls) -> dict:
        """Get MCP agent client configuration"""
        return {
            "http_tools_server": {
                "url": cls.MCP_SEMANTIC_URL,
                "transport": "streamable_http",
            }
        }

    @classmethod
    def get_mcp_workflow_client_config(cls) -> dict:
        """Get MCP workflow client configuration"""
        return {
            "http_tools_server": {
                "url": cls.MCP_WORKFLOW_URL,
                "transport": "streamable_http",
            }
        }

    @classmethod
    def is_using_google(cls) -> bool:
        return cls.MODEL_PROVIDER == "google"

    @classmethod
    def is_using_ollama(cls) -> bool:
        return cls.MODEL_PROVIDER == "ollama"

    @classmethod
    def is_using_bedrock(cls) -> bool:
        return cls.MODEL_PROVIDER == "bedrock"

    @classmethod
    def _bedrock_base_cfg(cls) -> dict:
        cfg = {"temperature": cls.LLM_TEMPERATURE, "region_name": cls.AWS_REGION}
        if cls.BEDROCK_AWS_ACCESS_KEY_ID and cls.BEDROCK_AWS_SECRET_ACCESS_KEY:
            cfg["aws_access_key_id"] = cls.BEDROCK_AWS_ACCESS_KEY_ID
            cfg["aws_secret_access_key"] = cls.BEDROCK_AWS_SECRET_ACCESS_KEY
        return cfg

    @classmethod
    def get_llm_config(cls, model: str = None) -> dict:
        if cls.is_using_google():
            cfg = {"model": cls.GOOGLE_LLM_MODEL, "temperature": cls.LLM_TEMPERATURE, "google_api_key": cls.GOOGLE_API_KEY}
            return {**cfg, "model": model} if model else cfg
        if cls.is_using_bedrock():
            cfg = {**cls._bedrock_base_cfg(), "model_id": cls.BEDROCK_LLM_MODEL}
            return {**cfg, "model_id": model} if model else cfg
        cfg = {"model": cls.LLM_MODEL, "temperature": cls.LLM_TEMPERATURE, "base_url": cls.OLLAMA_BASE_URL, "reasoning": False, "num_ctx": 8192, "keep_alive": -1}
        return {**cfg, "model": model} if model else cfg

    @classmethod
    def get_llm_config_no_think(cls, model: str = None) -> dict:
        if cls.is_using_google():
            cfg = {"model": cls.GOOGLE_CHAT_LLM_MODEL, "temperature": cls.LLM_TEMPERATURE, "google_api_key": cls.GOOGLE_API_KEY}
            return {**cfg, "model": model} if model else cfg
        if cls.is_using_bedrock():
            cfg = {**cls._bedrock_base_cfg(), "model_id": cls.BEDROCK_CHAT_LLM_MODEL}
            return {**cfg, "model_id": model} if model else cfg
        cfg = {"model": cls.CHAT_LLM_MODEL, "temperature": cls.LLM_TEMPERATURE, "base_url": cls.OLLAMA_BASE_URL, "reasoning": False, "num_ctx": 8192, "keep_alive": -1}
        return {**cfg, "model": model} if model else cfg

    @classmethod
    def get_resolver_model(cls) -> str:
        if cls.is_using_google():
            return cls.GOOGLE_RESOLVER_LLM_MODEL
        if cls.is_using_bedrock():
            return cls.BEDROCK_RESOLVER_LLM_MODEL
        return cls.RESOLVER_LLM_MODEL

    @classmethod
    def get_dynamodb_config(cls) -> dict:
        """Get DynamoDB configuration"""
        dynamodb_config = {"region_name": cls.AWS_REGION}
        if cls.DYNAMODB_ENDPOINT:
            dynamodb_config["endpoint_url"] = cls.DYNAMODB_ENDPOINT
        if cls.AWS_ACCESS_KEY_ID and cls.AWS_SECRET_ACCESS_KEY:
            dynamodb_config["aws_access_key_id"] = cls.AWS_ACCESS_KEY_ID
            dynamodb_config["aws_secret_access_key"] = cls.AWS_SECRET_ACCESS_KEY
        elif cls.is_local_dynamodb():
            dynamodb_config["aws_access_key_id"] = "local"
            dynamodb_config["aws_secret_access_key"] = "local"
        return dynamodb_config

    @classmethod
    def get_postgres_config(cls) -> dict:
        """Get PostgreSQL configuration (using PG_* variables)"""
        return {
            "host": cls.POSTGRES_HOST,
            "port": cls.POSTGRES_PORT,
            "database": cls.POSTGRES_DB,
            "user": cls.POSTGRES_USER,
            "password": cls.POSTGRES_PASSWORD
        }

    @classmethod
    def is_local_dynamodb(cls) -> bool:
        """Check if configured DynamoDB endpoint is local."""
        if not cls.DYNAMODB_ENDPOINT:
            return False
        return any(
            token in cls.DYNAMODB_ENDPOINT
            for token in ("localhost", "127.0.0.1", "dynamodb-local")
        )

    @classmethod
    def is_using_dynamodb(cls) -> bool:
        """Check if using DynamoDB"""
        return cls.DB_TYPE == "dynamodb"

    @classmethod
    def is_using_postgres(cls) -> bool:
        """Check if using PostgreSQL"""
        return cls.DB_TYPE == "postgres"

    @classmethod
    def get_neo4j_config(cls) -> dict:
        """Get Neo4j configuration for graph database"""
        if getattr(cls, "NEO4J_URI", None):
            uri = cls.NEO4J_URI
        else:
            uri = f"neo4j://{cls.NEO4J_HOST}:{cls.NEO4J_PORT}"
        return {
            "uri": uri,
            "user": cls.NEO4J_USER,
            "password": cls.NEO4J_PASSWORD,
            "database": cls.NEO4J_DATABASE
        }

    @classmethod
    def is_neo4j_enabled(cls) -> bool:
        """Check if Neo4j is enabled for hierarchy queries"""
        return cls.NEO4J_ENABLED

    @classmethod
    def is_guardian_enabled(cls) -> bool:
        """Check if Guardian pre-flight checks are enabled"""
        return cls.GUARDIAN_ENABLED

    @classmethod
    def is_reflection_enabled(cls) -> bool:
        """Check if the reflection policy is enabled for Guardian blockings."""
        return cls.REFLECTION_ENABLED
    

    @classmethod
    def validate(cls) -> None:
        """Validate critical configuration on startup.
        
        Raises:
            ValueError: If required configuration is missing or invalid
        """
        errors = []
        
        # Validate required database config
        if not cls.AWS_REGION:
            errors.append("AWS_REGION is required but not set")
        if cls.is_using_dynamodb():
            if (cls.AWS_ACCESS_KEY_ID and not cls.AWS_SECRET_ACCESS_KEY) or (
                cls.AWS_SECRET_ACCESS_KEY and not cls.AWS_ACCESS_KEY_ID
            ):
                errors.append("Both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set together")
            # DYNAMODB_ENDPOINT is optional for AWS IAM role / default credential chain usage
        
        # Validate MCP server config
        if cls.MCP_CORE_PORT < 1 or cls.MCP_CORE_PORT > 65535:
            errors.append(f"MCP_CORE_PORT must be between 1-65535, got {cls.MCP_CORE_PORT}")
        
        # Validate LLM configuration
        if not cls.LLM_MODEL:
            errors.append("LLM_MODEL is required but not set")
        if not cls.OLLAMA_BASE_URL:
            errors.append("OLLAMA_BASE_URL is required but not set")
        
        if errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        logger.info("Configuration validation passed")


# Make config instance available for import
config = Config()
