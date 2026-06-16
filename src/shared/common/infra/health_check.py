"""Health check utilities for service dependencies.

Verifies critical services are available before agent operations.
Used during agent initialization to fail fast with clear errors.
"""

import asyncio
import time
from typing import Dict, Tuple
from dataclasses import dataclass
from enum import Enum
from shared.common.agents.bridge import traceable
from shared.config import config
from shared.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


class HealthStatus(Enum):
    """Health check status."""
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"


@dataclass
class ServiceHealth:
    """Health check result for a single service."""
    service_name: str
    status: HealthStatus
    message: str
    latency_ms: float = 0.0


class HealthChecker:
    """Check health of all critical services."""
    
    @traceable(name="check_ollama_health")
    async def check_ollama(self) -> ServiceHealth:
        """Check Ollama LLM service availability."""
        try:
            start = time.time()
            
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{config.OLLAMA_BASE_URL}/api/tags",
                    follow_redirects=True
                )
            
            latency = (time.time() - start) * 1000
            
            if response.status_code == 200:
                return ServiceHealth(
                    service_name="Ollama LLM",
                    status=HealthStatus.HEALTHY,
                    message=f"Connected to {config.LLM_MODEL}",
                    latency_ms=latency
                )
            else:
                return ServiceHealth(
                    service_name="Ollama LLM",
                    status=HealthStatus.UNHEALTHY,
                    message=f"Status {response.status_code}",
                    latency_ms=latency
                )
                
        except Exception as e:
            return ServiceHealth(
                service_name="Ollama LLM",
                status=HealthStatus.UNHEALTHY,
                message=f"Cannot reach {config.OLLAMA_BASE_URL}: {str(e)[:100]}"
            )
    
    @traceable(name="check_mcp_server_health")
    async def check_mcp_server(self) -> ServiceHealth:
        """Check MCP Server availability."""
        try:
            start = time.time()
            
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"http://{config.SERVER_HOST}:{config.MCP_CORE_PORT}/health",
                    follow_redirects=True
                )
            
            latency = (time.time() - start) * 1000
            
            if response.status_code in [200, 404]:
                return ServiceHealth(
                    service_name="MCP Server",
                    status=HealthStatus.HEALTHY,
                    message=f"Connected to {config.MCP_CORE_URL}",
                    latency_ms=latency
                )
            else:
                return ServiceHealth(
                    service_name="MCP Server",
                    status=HealthStatus.UNHEALTHY,
                    message=f"Status {response.status_code}",
                    latency_ms=latency
                )
                
        except Exception as e:
            return ServiceHealth(
                service_name="MCP Server",
                status=HealthStatus.UNHEALTHY,
                message=f"Cannot reach {config.MCP_CORE_URL}: {str(e)[:100]}"
            )
    
    @traceable(name="check_database_health")
    async def check_database(self) -> ServiceHealth:
        """Check database connectivity."""
        try:
            if config.is_using_postgres():
                return await self._check_postgres()
            elif config.is_using_dynamodb():
                return await self._check_dynamodb()
            else:
                return ServiceHealth(
                    service_name="Database",
                    status=HealthStatus.UNHEALTHY,
                    message=f"Unknown DB_TYPE: {config.DB_TYPE}"
                )
                
        except Exception as e:
            return ServiceHealth(
                service_name="Database",
                status=HealthStatus.UNHEALTHY,
                message=f"Database error: {str(e)[:100]}"
            )
    
    async def _check_postgres(self) -> ServiceHealth:
        """Check PostgreSQL connectivity."""
        try:
            import psycopg2
            start = time.time()
            
            conn = psycopg2.connect(
                host=config.POSTGRES_HOST,
                port=config.POSTGRES_PORT,
                database=config.POSTGRES_DB,
                user=config.POSTGRES_USER,
                password=config.POSTGRES_PASSWORD,
                connect_timeout=5
            )
            
            latency = (time.time() - start) * 1000
            conn.close()
            
            return ServiceHealth(
                service_name="PostgreSQL Database",
                status=HealthStatus.HEALTHY,
                message=f"Connected to {config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}",
                latency_ms=latency
            )
            
        except Exception as e:
            return ServiceHealth(
                service_name="PostgreSQL Database",
                status=HealthStatus.UNHEALTHY,
                message=f"Cannot connect: {str(e)[:100]}"
            )
    
    async def _check_dynamodb(self) -> ServiceHealth:
        """Check DynamoDB connectivity."""
        try:
            import boto3
            from botocore.exceptions import ClientError
            start = time.time()
            
            dynamodb = boto3.resource(
                'dynamodb',
                endpoint_url=config.DYNAMODB_ENDPOINT,
                region_name=config.AWS_REGION,
                aws_access_key_id=config.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY
            )
            
            tables = list(dynamodb.tables.all())
            latency = (time.time() - start) * 1000
            
            return ServiceHealth(
                service_name="DynamoDB",
                status=HealthStatus.HEALTHY,
                message=f"Connected ({len(tables)} tables)",
                latency_ms=latency
            )
            
        except Exception as e:
            return ServiceHealth(
                service_name="DynamoDB",
                status=HealthStatus.UNHEALTHY,
                message=f"Cannot connect: {str(e)[:100]}"
            )
    
    @traceable(name="check_neo4j_health")
    async def check_neo4j(self) -> ServiceHealth:
        """Check Neo4j connectivity (optional)."""
        if not config.is_neo4j_enabled():
            return ServiceHealth(
                service_name="Neo4j",
                status=HealthStatus.DEGRADED,
                message="Neo4j disabled in configuration"
            )
        
        try:
            from neo4j import AsyncGraphDatabase
            start = time.time()
            
            uri = getattr(config, "NEO4J_URI", None) or f"neo4j://{config.NEO4J_HOST}:{config.NEO4J_PORT}"
            driver = AsyncGraphDatabase.driver(
                uri,
                auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
            )
            
            async with driver.session() as session:
                await session.run("RETURN 1")
            
            latency = (time.time() - start) * 1000
            await driver.close()
            
            return ServiceHealth(
                service_name="Neo4j",
                status=HealthStatus.HEALTHY,
                message=f"Connected to {uri}",
                latency_ms=latency
            )
            
        except ImportError:
            return ServiceHealth(
                service_name="Neo4j",
                status=HealthStatus.DEGRADED,
                message="neo4j-driver not installed"
            )
        except Exception as e:
            return ServiceHealth(
                service_name="Neo4j",
                status=HealthStatus.UNHEALTHY,
                message=f"Cannot connect: {str(e)[:100]}"
            )


@traceable(name="check_all_services")
async def check_all_services(critical_only: bool = True) -> Dict[str, ServiceHealth]:
    """Check all critical services.
    
    Args:
        critical_only: If True, only check services required for agents.
                      If False, also check optional services like Neo4j.
    
    Returns:
        Dict of service_name -> ServiceHealth
    """
    checker = HealthChecker()
    
    checks = [
        checker.check_ollama(),
        checker.check_mcp_server(),
        checker.check_database(),
    ]
    
    if not critical_only:
        checks.append(checker.check_neo4j())
    
    results = await asyncio.gather(*checks, return_exceptions=True)
    
    services = {}
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Health check exception: {result}")
            continue
        services[result.service_name] = result
    
    return services


@traceable(name="verify_services_healthy")
async def verify_services_healthy(raise_on_error: bool = True) -> Tuple[bool, str]:
    """Verify all critical services are healthy.
    
    Args:
        raise_on_error: If True, raise exception on unhealthy service.
                       If False, return status and message.
    
    Returns:
        Tuple of (all_healthy: bool, summary_message: str)
        
    Raises:
        RuntimeError: If raise_on_error=True and any service unhealthy
    """
    services = await check_all_services(critical_only=True)
    
    unhealthy = {
        name: health for name, health in services.items()
        if health.status == HealthStatus.UNHEALTHY
    }
    
    lines = ["Service Health Check:"]
    for name, health in services.items():
        status_symbol = "✓" if health.status == HealthStatus.HEALTHY else "✗"
        latency_str = f" ({health.latency_ms:.0f}ms)" if health.latency_ms > 0 else ""
        lines.append(f"  {status_symbol} {name}: {health.message}{latency_str}")
    
    summary = "\n".join(lines)
    logger.info(summary)
    
    if unhealthy:
        error_msg = f"Unhealthy services: {', '.join(unhealthy.keys())}\n{summary}"
        if raise_on_error:
            raise RuntimeError(error_msg)
        else:
            return False, error_msg
    
    return True, summary


def print_health_report(services: Dict[str, ServiceHealth]) -> None:
    logger.debug("\n" + "="*70)
    logger.debug("SERVICE HEALTH REPORT".center(70))
    logger.debug("="*70)
    
    healthy_count = sum(1 for s in services.values() if s.status == HealthStatus.HEALTHY)
    total = len(services)
    
    for name, health in services.items():
        status_icon = {
            HealthStatus.HEALTHY: "✓ GREEN",
            HealthStatus.DEGRADED: "⚠ YELLOW",
            HealthStatus.UNHEALTHY: "✗ RED"
        }[health.status]
        
        latency_str = f" [{health.latency_ms:.0f}ms]" if health.latency_ms > 0 else ""
        
        logger.debug(f"\n{name}:")
        logger.debug(f"  Status: {status_icon}{latency_str}")
        logger.debug(f"  Details: {health.message}")
    
    logger.debug(f"\n{'='*70}")
    logger.debug(f"Summary: {healthy_count}/{total} services healthy")
    logger.debug("="*70 + "\n")


async def ensure_services_ready() -> bool:
    """Ensure all services are ready before proceeding.
    
    Call this in agent initialization:
        if not await ensure_services_ready():
            raise RuntimeError("Services not ready")
    
    Returns:
        True if all services healthy, False otherwise
    """
    try:
        is_healthy, message = await verify_services_healthy(raise_on_error=False)
        if not is_healthy:
            logger.error(f"Services not ready:\n{message}")
        return is_healthy
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return False


if __name__ == "__main__":
    """Run health checks from command line."""
    
    async def main():
        logger.debug("\nRunning health checks...\n")
        services = await check_all_services(critical_only=False)
        print_health_report(services)
        
        unhealthy = sum(1 for s in services.values() if s.status == HealthStatus.UNHEALTHY)
        exit(1 if unhealthy > 0 else 0)
    
    asyncio.run(main())
