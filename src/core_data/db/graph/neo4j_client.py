"""Neo4j graph database client for hierarchy and relationship analytics."""

import functools
import threading
from typing import List, Dict, Any
from neo4j import GraphDatabase, exceptions as neo4j_exceptions

from shared.config.config_common import Config
from shared.common.log.logging_config import setup_structured_logging
from shared.common.infra.error_helpers import best_effort

logger = setup_structured_logging(__name__)


def _graph_op(default):
    """Guard + catch: returns `default` when disconnected or on any exception."""
    def decorator(func):
        op = func.__name__.replace("_", " ")
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            if not self.connected:
                return type(default)() if isinstance(default, (list, dict)) else default
            try:
                return func(self, *args, **kwargs)
            except Exception as exc:
                logger.error(f"Failed to {op}: {exc}")
                return type(default)() if isinstance(default, (list, dict)) else default
        return wrapper
    return decorator


class Neo4jClient:

    def __init__(self):
        self.config = Config.get_neo4j_config()
        self.driver = None
        self.connected = False

        if Config.is_neo4j_enabled():
            try:
                self._connect()
            except Exception as e:
                logger.warning(f"Failed to connect to Neo4j: {e}. Graph queries will be unavailable.")
                self.connected = False

    def _connect(self):
        try:
            uri = self.config.get("uri") or f"neo4j://{self.config['host']}:{self.config['port']}"
            self.driver = GraphDatabase.driver(
                uri,
                auth=(self.config['user'], self.config['password']),
                database=self.config['database'],
                notifications_min_severity="WARNING",
            )
            with self.driver.session() as session:
                session.run("RETURN 1")
            self.connected = True
            logger.debug(f"Connected to Neo4j at {uri}")
            self._setup_constraints()
            self._setup_fulltext_indexes()
            self._setup_property_indexes()
            self._setup_vector_index()
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            self.connected = False
            raise

    @best_effort(message="Could not provision Neo4j constraints")
    def _setup_constraints(self) -> None:
        constraints = [
            "CREATE CONSTRAINT applicant_id_unique IF NOT EXISTS FOR (a:Applicant) REQUIRE a.applicant_id IS UNIQUE",
        ]
        with self.driver.session(database=self.config["database"]) as session:
            for constraint in constraints:
                try:
                    session.run(constraint)
                except Exception as e:
                    logger.warning(f"Constraint skipped (may already exist): {e}")
        logger.debug("Neo4j constraints verified")

    @best_effort(message="Could not provision Neo4j fulltext indexes")
    def _setup_fulltext_indexes(self) -> None:
        fulltext_specs = [
            {
                "name": "applicant_fulltext_index",
                "label": "Applicant",
                "properties": ["applicant_name", "policy_type", "location", "risk_tier"],
            }
        ]

        def _old_fulltext_statement(spec: dict) -> str:
            props = ",".join(f"'{p}'" for p in spec["properties"])
            return (
                f"CALL db.index.fulltext.createNodeIndex('{spec['name']}', ['{spec['label']}'], [{props}])"
            )

        def _new_fulltext_statement(spec: dict) -> str:
            props = ", ".join(f"n.{p}" for p in spec["properties"])
            return (
                f"CREATE FULLTEXT INDEX {spec['name']} IF NOT EXISTS FOR (n:{spec['label']}) ON EACH [{props}]"
            )

        with self.driver.session(database=self.config["database"]) as session:
            for spec in fulltext_specs:
                try:
                    session.run(_old_fulltext_statement(spec))
                except neo4j_exceptions.ClientError as client_error:
                    if getattr(client_error, "code", "").startswith("Neo.ClientError.Procedure."):
                        try:
                            session.run(_new_fulltext_statement(spec))
                        except Exception as new_error:
                            logger.warning(
                                f"Could not create Neo4j fulltext index '{spec['name']}' with fallback syntax: {new_error}"
                            )
                    else:
                        logger.warning(f"Fulltext index skipped or failed: {client_error}")
                except Exception as e:
                    logger.warning(f"Fulltext index skipped or failed: {e}")
        logger.debug("Neo4j fulltext indexes verified")

    @best_effort(message="Could not provision Neo4j vector index")
    def _setup_vector_index(self) -> None:
        """Create HNSW vector index on applicant embeddings for semantic search."""
        dim = getattr(Config, "PGVECTOR_EMBEDDING_DIM", 1024)
        with self.driver.session(database=self.config["database"]) as session:
            records = list(session.run(
                "SHOW VECTOR INDEXES WHERE name = 'applicant-embedding'"
            ))
            if records:
                existing_dim = (records[0].get("options") or {}).get("indexConfig", {}).get("vector.dimensions")
                if existing_dim and int(existing_dim) != dim:
                    logger.warning(
                        "Neo4j vector index dim mismatch: existing=%d target=%d — dropping and recreating",
                        int(existing_dim), dim,
                    )
                    session.run("DROP INDEX `applicant-embedding`")
            session.run(
                """
                CREATE VECTOR INDEX `applicant-embedding` IF NOT EXISTS
                FOR (a:Applicant) ON (a.embedding)
                OPTIONS {indexConfig: {
                    `vector.dimensions`: $dim,
                    `vector.similarity_function`: 'cosine'
                }}
                """,
                {"dim": dim},
            )
        logger.debug("Neo4j vector index verified (dim=%d)", dim)

    @best_effort(message="Could not provision Neo4j property indexes")
    def _setup_property_indexes(self) -> None:
        """Provision B-tree indexes on frequently filtered Applicant properties."""
        indexes = [
            "CREATE INDEX applicant_policy_type IF NOT EXISTS FOR (a:Applicant) ON (a.policy_type)",
            "CREATE INDEX applicant_risk_tier IF NOT EXISTS FOR (a:Applicant) ON (a.risk_tier)",
            "CREATE INDEX applicant_location IF NOT EXISTS FOR (a:Applicant) ON (a.location)",
        ]
        with self.driver.session(database=self.config["database"]) as session:
            for index in indexes:
                try:
                    session.run(index)
                except Exception as e:
                    logger.warning(f"Property index skipped (may already exist): {e}")
        logger.debug("Neo4j property indexes verified")

    def close(self):
        if self.driver:
            self.driver.close()
            self.connected = False
            logger.info("Neo4j connection closed")

    def is_connected(self) -> bool:
        return self.connected

    def run_query(self, query: str, parameters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        if not self.connected:
            logger.warning("Neo4j not connected, query will not execute")
            return []
        try:
            with self.driver.session(database=self.config['database']) as session:
                result = session.run(query, parameters or {})
                return [dict(record) for record in result]
        except Exception as e:
            logger.error(f"Neo4j query failed: {e}", exc_info=True)
            raise


    @_graph_op(False)
    def upsert_applicant_properties(
        self,
        applicant_id: str,
        name: str,
        policy_type: str,
        location: str,
        risk_tier: str = "STANDARD",
        contact_email: str | None = None,
        contact_phone: str | None = None,
        enterprise_identifier: str | None = None,
        state_or_province_code: str | None = None,
        country_code: str | None = None,
        postal_code: str | None = None,
    ) -> bool:
        """Enrich an Applicant node with intake fields for similarity scoring."""
        self.run_query(
            """
            MERGE (a:Applicant {applicant_id: $applicant_id})
            SET a.applicant_name          = $name,
                a.policy_type             = $policy_type,
                a.location                = $location,
                a.risk_tier               = $risk_tier,
                a.contact_email           = $contact_email,
                a.contact_phone           = $contact_phone,
                a.enterprise_identifier   = $enterprise_identifier,
                a.state_or_province_code  = $state_or_province_code,
                a.country_code            = $country_code,
                a.postal_code             = $postal_code
            """,
            {
                "applicant_id": applicant_id,
                "name": name,
                "policy_type": policy_type,
                "location": location,
                "risk_tier": risk_tier,
                "contact_email": contact_email or None,
                "contact_phone": contact_phone or None,
                "enterprise_identifier": enterprise_identifier or None,
                "state_or_province_code": state_or_province_code or None,
                "country_code": country_code or None,
                "postal_code": postal_code or None,
            },
        )
        return True

    @_graph_op(False)
    def upsert_applicant_embedding(self, applicant_id: str, text: str, embedding: List[float]) -> bool:
        """Store a float-array embedding on an :Applicant node for vector search."""
        self.run_query(
            """
            MERGE (a:Applicant {applicant_id: $applicant_id})
            SET a.embedding      = $embedding,
                a.embedding_text = $text
            """,
            {"applicant_id": applicant_id, "embedding": embedding, "text": text},
        )
        logger.debug("upsert_applicant_embedding applicant_id=%s dim=%d", applicant_id, len(embedding))
        return True

    @_graph_op([])
    def vector_search_applicants(
        self,
        query_embedding: List[float],
        limit: int = 5,
        applicant_id: str | None = None,
        alpha: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """ANN search via the applicant-embedding HNSW index, with optional self-exclusion."""
        query = """
            CALL db.index.vector.queryNodes('applicant-embedding', $limit, $embedding)
            YIELD node AS a, score AS vector_score
            WHERE ($applicant_id IS NULL OR a.applicant_id <> $applicant_id)
            RETURN a.applicant_id  AS applicant_id,
                   a.applicant_name AS name,
                   a.policy_type   AS policy_type,
                   a.location      AS location,
                   a.risk_tier     AS risk_tier,
                   vector_score
            LIMIT $limit
        """
        return self.run_query(query, {
            "embedding": query_embedding,
            "limit": limit,
            "applicant_id": applicant_id,
        })

    @_graph_op(0)
    def bulk_upsert_similar_to_edges(self, edges: List[Dict[str, Any]]) -> int:
        """MERGE :SIMILAR_TO edges in one Cypher batch. edges: [{source_id, target_id, rel_type, score}]."""
        if not edges:
            return 0
        self.run_query(
            """
            UNWIND $edges AS e
            MATCH (a:Applicant {applicant_id: e.source_id})
            MATCH (b:Applicant {applicant_id: e.target_id})
            MERGE (a)-[r:SIMILAR_TO]->(b)
            SET r.rel_type = e.rel_type,
                r.score    = e.score
            """,
            {"edges": edges},
        )
        logger.info("bulk_upsert_similar_to_edges count=%d", len(edges))
        return len(edges)

    @_graph_op([])
    def find_similar_applicants(self, applicant_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Score applicants by policy type and location similarity."""
        query = """
            MATCH (target:Applicant {applicant_id: $applicant_id})
            MATCH (a:Applicant)
            WHERE a.applicant_id <> target.applicant_id
            WITH a, target,
                 (CASE WHEN a.policy_type = target.policy_type THEN 1.0 ELSE 0.0 END +
                  CASE WHEN a.location = target.location THEN 0.5 ELSE 0.0 END) AS score
            WHERE score > 0.0
            ORDER BY score DESC
            LIMIT $limit
            RETURN a.applicant_id AS applicant_id,
                   a.applicant_name AS name,
                   a.policy_type AS policy_type,
                   a.location AS location,
                   a.risk_tier AS risk_tier,
                   score
        """
        return self.run_query(query, {"applicant_id": applicant_id, "limit": limit})

    @_graph_op({})
    def get_applicant_context(self, applicant_id: str) -> Dict[str, Any]:
        result = self.run_query(
            """
            MATCH (a:Applicant {applicant_id: $applicant_id})
            OPTIONAL MATCH (a)-[:SIMILAR_TO]-(similar:Applicant)
            WHERE similar.applicant_id <> a.applicant_id
            RETURN a.applicant_id AS applicant_id,
                   a.applicant_name AS name,
                   a.policy_type AS policy_type,
                   a.location AS location,
                   a.risk_tier AS risk_tier,
                   collect(DISTINCT {id: similar.applicant_id, name: similar.applicant_name}) AS similar_applicants
            """,
            {"applicant_id": applicant_id},
        )
        return result[0] if result else {}


_neo4j_client = None
_neo4j_client_lock = threading.Lock()


def get_neo4j_client() -> Neo4jClient:
    global _neo4j_client
    if _neo4j_client is None:
        with _neo4j_client_lock:
            if _neo4j_client is None:
                _neo4j_client = Neo4jClient()
    return _neo4j_client


def close_neo4j():
    global _neo4j_client
    if _neo4j_client:
        _neo4j_client.close()
        _neo4j_client = None
