"""Knowledge graph for applicant relationships, risk analysis, and similarity queries."""

import json
import re
import threading
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path

from mltek.common.log import setup_structured_logging
from core_data.db.graph.neo4j_client import get_neo4j_client
from core_data.db.graph.graph_operations import get_graph_operations

logger = setup_structured_logging(__name__)


class ApplicantRelationship:
    """Represents a relationship between applicants."""

    def __init__(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str,
        metadata: Optional[Dict] = None,
    ):
        self.source_id = source_id
        self.target_id = target_id
        self.relationship_type = relationship_type  # "policy_type", "location", "risk_tier", "network"
        self.metadata = metadata or {}
        self.created_at = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relationship_type": self.relationship_type,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ApplicantRelationship":
        return cls(
            source_id=data["source_id"],
            target_id=data["target_id"],
            relationship_type=data["relationship_type"],
            metadata=data.get("metadata"),
        )


class ApplicantNode:
    """Represents an applicant node in the knowledge graph."""

    def __init__(
        self,
        applicant_id: str,
        name: str,
        policy_type: str,
        location: str,
        risk_tier: str = "unknown",
        contact_email: Optional[str] = None,
        contact_phone: Optional[str] = None,
        enterprise_identifier: Optional[str] = None,
        state_or_province_code: Optional[str] = None,
        country_code: Optional[str] = None,
        postal_code: Optional[str] = None,
        declared_value: Optional[str] = None,
    ):
        self.applicant_id = applicant_id
        self.name = name
        self.policy_type = policy_type
        self.location = location
        self.risk_tier = risk_tier
        self.contact_email = contact_email
        self.contact_phone = contact_phone
        self.enterprise_identifier = enterprise_identifier
        self.state_or_province_code = state_or_province_code
        self.country_code = country_code
        self.postal_code = postal_code
        self.declared_value = declared_value
        self.created_at = datetime.now().isoformat()
        self.relationships: List[ApplicantRelationship] = []

    def add_relationship(self, relationship: ApplicantRelationship):
        self.relationships.append(relationship)

    def to_dict(self) -> Dict:
        return {
            "applicant_id": self.applicant_id,
            "name": self.name,
            "policy_type": self.policy_type,
            "location": self.location,
            "risk_tier": self.risk_tier,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "enterprise_identifier": self.enterprise_identifier,
            "state_or_province_code": self.state_or_province_code,
            "country_code": self.country_code,
            "postal_code": self.postal_code,
            "declared_value": self.declared_value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ApplicantNode":
        return cls(
            applicant_id=data["applicant_id"],
            name=data["name"],
            policy_type=data["policy_type"],
            location=data["location"],
            risk_tier=data.get("risk_tier", "unknown"),
            contact_email=data.get("contact_email"),
            contact_phone=data.get("contact_phone"),
            enterprise_identifier=data.get("enterprise_identifier"),
            state_or_province_code=data.get("state_or_province_code"),
            country_code=data.get("country_code"),
            postal_code=data.get("postal_code"),
            declared_value=data.get("declared_value"),
        )


class GraphService:
    """
    In-memory knowledge graph for applicant relationships.

    Manages applicant nodes and relationships, and provides similarity search,
    risk clustering, network traversal, and keyword-based queries.

    Neo4j is the authoritative store; the in-memory graph is a fallback when
    Neo4j is unavailable. File persistence is optional and disabled by default.
    """

    def __init__(self, data_file: Optional[str] = None, applicant_service: Any = None):
        self.data_file = Path(data_file) if data_file else None
        self.applicant_service = applicant_service
        self.applicants: Dict[str, ApplicantNode] = {}
        self.relationships: Dict[str, ApplicantRelationship] = {}
        self._sync_lock = threading.Lock()
        self.load()

    def sync_from_service(self, applicant_service) -> int:
        """Replace in-memory graph with live data from database and enrich Neo4j nodes.

        Returns the number of applicants loaded.
        """
        with self._sync_lock:
            return self._sync_from_service_locked(applicant_service)

    def _sync_from_service_locked(self, applicant_service) -> int:
        applicants = applicant_service.list_all(limit=10000)
        self.applicants.clear()
        self.relationships.clear()

        neo4j = get_neo4j_client()

        for applicant in applicants:
            node = self._node_from_db(applicant)
            self.applicants[node.applicant_id] = node

            if neo4j.is_connected():
                neo4j.upsert_applicant_properties(
                    applicant_id=node.applicant_id,
                    name=node.name,
                    policy_type=node.policy_type,
                    location=node.location,
                    risk_tier=node.risk_tier,
                    contact_email=node.contact_email,
                    contact_phone=node.contact_phone,
                    enterprise_identifier=node.enterprise_identifier,
                    state_or_province_code=node.state_or_province_code,
                    country_code=node.country_code,
                    postal_code=node.postal_code,
                )

        self._build_relationships()

        if neo4j.is_connected() and self.relationships:
            edges = [
                {
                    "source_id": rel.source_id,
                    "target_id": rel.target_id,
                    "rel_type":  rel.relationship_type,
                    "score":     1.0,
                }
                for rel in self.relationships.values()
                if rel.source_id < rel.target_id  # write each undirected edge once
            ]
            neo4j.bulk_upsert_similar_to_edges(edges)

        return len(self.applicants)

    @staticmethod
    def _compute_risk_tier(applicant: Any) -> str:
        """Classify risk tier from applicant data.

        This is a coarse classification proxy derived from underwriting rules,
        NOT a real risk score. It has no connection to actual claims history.
        Downstream agents must treat it as a starting-point category.
        """
        metadata = getattr(applicant, "metadata", {}) or {}
        risk_tier = metadata.get("risk_tier")
        if risk_tier:
            return str(risk_tier).upper()

        risk_profile = getattr(applicant, "risk_profile", None)
        if risk_profile:
            prior_claims = getattr(risk_profile, "prior_claims_count", None)
            if prior_claims and int(prior_claims or 0) > 2:
                return "HIGH_RISK"

        return "STANDARD"

    @staticmethod
    def _node_from_db(applicant: Any) -> "ApplicantNode":
        """Convert applicant domain object to ApplicantNode."""
        location_address = getattr(applicant, "location_address", None) or {}

        if not isinstance(location_address, dict) and hasattr(location_address, "dict"):
            location_address = location_address.dict()

        city = location_address.get("city") if isinstance(location_address, dict) else None
        state = location_address.get("state_or_province_code") if isinstance(location_address, dict) else None
        location = f"{city}, {state}" if city and state else city or state or "Unknown"

        contact_info = getattr(applicant, "contact_info", None)
        contact_email = None
        contact_phone = None
        if contact_info:
            if isinstance(contact_info, dict):
                contact_email = contact_info.get("email")
                contact_phone = contact_info.get("phone")
            else:
                contact_email = getattr(contact_info, "email", None)
                contact_phone = getattr(contact_info, "phone", None)

        declared_value = getattr(applicant, "declared_value", None)

        return ApplicantNode(
            applicant_id=applicant.applicant_id,
            name=getattr(applicant, "applicant_name", None) or applicant.applicant_id,
            policy_type=getattr(applicant, "policy_type", None) or "UNKNOWN",
            location=location,
            risk_tier=GraphService._compute_risk_tier(applicant),
            contact_email=contact_email,
            contact_phone=contact_phone,
            enterprise_identifier=getattr(applicant, "enterprise_identifier", None),
            state_or_province_code=state,
            country_code=location_address.get("country_code") if isinstance(location_address, dict) else None,
            postal_code=location_address.get("postal_code") if isinstance(location_address, dict) else None,
            declared_value=declared_value,
        )

    def _build_relationships(self) -> None:
        """Compute typed similarity edges between all applicant pairs (in-memory graph).

        Edge types (in priority order):
          policy_type  — same policy type (HOME, AUTO, COMMERCIAL, LIFE)
          risk_tier    — same risk classification
          location     — same state/city
        """
        def _add_edge(src: "ApplicantNode", dst: "ApplicantNode", rel_type: str, metadata: Optional[Dict] = None) -> None:
            fwd = ApplicantRelationship(src.applicant_id, dst.applicant_id, rel_type, metadata=metadata)
            rev = ApplicantRelationship(dst.applicant_id, src.applicant_id, rel_type, metadata=metadata)
            self.relationships[f"{src.applicant_id}-{dst.applicant_id}-{rel_type}"] = fwd
            self.relationships[f"{dst.applicant_id}-{src.applicant_id}-{rel_type}"] = rev
            src.relationships.append(fwd)
            dst.relationships.append(rev)

        applicant_list = list(self.applicants.values())
        for i, a1 in enumerate(applicant_list):
            for a2 in applicant_list[i + 1:]:
                # Policy type edge (preferred)
                if a1.policy_type == a2.policy_type and a1.policy_type not in ("UNKNOWN", ""):
                    _add_edge(a1, a2, "policy_type", {"policy_type": a1.policy_type})

                # Risk tier edge
                if a1.risk_tier == a2.risk_tier and a1.risk_tier not in ("UNKNOWN", ""):
                    _add_edge(a1, a2, "risk_tier", {"risk_tier": a1.risk_tier})

                # Location edge — always computed independently
                if a1.location == a2.location and a1.location not in ("Unknown", ""):
                    _add_edge(a1, a2, "location")

    def get_applicant(self, applicant_id: str) -> Optional[ApplicantNode]:
        return self.applicants.get(applicant_id)

    def get_related_applicants(
        self, applicant_id: str, relationship_type: Optional[str] = None
    ) -> List[ApplicantNode]:
        if applicant_id not in self.applicants:
            return []
        applicant = self.applicants[applicant_id]
        return [
            self.applicants[rel.target_id]
            for rel in applicant.relationships
            if (relationship_type is None or rel.relationship_type == relationship_type)
            and rel.target_id in self.applicants
        ]

    @staticmethod
    def _similarity_score(a1: "ApplicantNode", a2: "ApplicantNode") -> float:
        """Score similarity between two applicants.

        Scoring:
          same policy_type   → 0.60
          same risk_tier     → 0.40
          same location      → 0.30
        """
        location_match = a1.location == a2.location and a1.location not in ("Unknown", "")
        policy_match = a1.policy_type == a2.policy_type and a1.policy_type not in ("UNKNOWN", "")
        risk_match = a1.risk_tier == a2.risk_tier and a1.risk_tier not in ("UNKNOWN", "")

        score = 0.0
        if policy_match:
            score += 0.60
        if risk_match:
            score += 0.40
        if location_match:
            score += 0.30

        return score

    def find_similar_applicants(
        self, applicant_id: str, limit: int = 5
    ) -> List[Tuple[ApplicantNode, float]]:
        """Score all applicants against target using policy type, risk tier, and location."""
        if applicant_id not in self.applicants:
            return []

        target = self.applicants[applicant_id]
        scored = [
            (applicant, self._similarity_score(target, applicant))
            for aid, applicant in self.applicants.items()
            if aid != applicant_id
        ]
        scored = [(a, s) for a, s in scored if s > 0]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def _format_applicant_query_results(self, query: str, results: List[Dict[str, Any]]) -> str:
        if not results:
            return f"No applicants matched the query: {query}"

        def _location(row: Dict[str, Any]) -> str:
            location = row.get("location")
            if location:
                return location
            city = row.get("city") or ""
            state = row.get("state") or ""
            if city and state:
                return f"{city}, {state}"
            if city:
                return city
            if state:
                return state
            return "Unknown"

        lines = []
        for row in results:
            name = row.get("name") or row.get("applicant_name") or row.get("applicant_id")
            applicant_id = row.get("applicant_id")
            policy_type = row.get("policy_type") or "unknown"
            location = _location(row)
            risk = row.get("risk_tier") or "unknown"
            email = row.get("contact_email")
            phone = row.get("contact_phone")
            line = (
                f"- {name} (id={applicant_id}, policy={policy_type}, location={location}, risk={risk})"
            )
            if email:
                line += f"\n    email: {email}"
            if phone:
                line += f"\n    phone: {phone}"
            lines.append(line)

        return f"Query: {query}\nMatches: {len(lines)}\n" + "\n".join(lines)

    _UUID_RE = re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    )

    @staticmethod
    def _applicant_matches_query(applicant: Any, terms: List[str]) -> bool:
        def _get_location_field(field_name: str) -> Optional[str]:
            location_address = getattr(applicant, "location_address", None)
            if location_address is None:
                return None
            if isinstance(location_address, dict):
                return location_address.get(field_name)
            if hasattr(location_address, "get"):
                return location_address.get(field_name)
            if hasattr(location_address, "dict"):
                return location_address.dict().get(field_name)
            return None

        def _get_contact_field(field_name: str) -> Optional[str]:
            contact_info = getattr(applicant, "contact_info", None)
            if contact_info is None:
                return None
            if isinstance(contact_info, dict):
                return contact_info.get(field_name)
            if hasattr(contact_info, "get"):
                return contact_info.get(field_name)
            return None

        text = " ".join(
            filter(None, [
                getattr(applicant, "applicant_name", None),
                getattr(applicant, "policy_type", None),
                getattr(applicant, "declared_value", None),
                getattr(applicant, "risk_tier", None),
                _get_location_field("city"),
                _get_location_field("state_or_province_code"),
                _get_contact_field("email"),
            ])
        ).lower()
        return any(term in text for term in terms)

    @staticmethod
    def _applicant_location_from_applicant(applicant: Any) -> str:
        location_address = getattr(applicant, "location_address", None) or {}
        if not isinstance(location_address, dict) and hasattr(location_address, "dict"):
            location_address = location_address.dict()
        city = location_address.get("city") if isinstance(location_address, dict) else None
        state = location_address.get("state_or_province_code") if isinstance(location_address, dict) else None
        if city and state:
            return f"{city}, {state}"
        return city or state or "Unknown"

    def query_applicant_graph_raw(self, query: str, applicant_service: Any = None) -> List[Dict[str, Any]]:
        """Return raw applicant graph query results without text formatting."""
        if not query or not isinstance(query, str):
            return []

        neo4j = get_neo4j_client()
        if neo4j.is_connected():
            try:
                return self._query_neo4j_or_memory_raw(query)
            except Exception as e:
                logger.warning(f"Neo4j query failed, falling through to DB: {e}")

        applicant_service = applicant_service or self.applicant_service
        if applicant_service is not None:
            return self._db_query_applicant_graph_raw(query, applicant_service)

        return self._query_neo4j_or_memory_raw(query)

    def _query_neo4j_or_memory_raw(self, query: str) -> List[Dict[str, Any]]:
        if not query or not isinstance(query, str):
            return []

        uuid_match = self._UUID_RE.search(query)
        neo4j = get_neo4j_client()
        if neo4j.is_connected():
            terms = [t for t in re.split(r"\W+", query.lower()) if len(t) >= 2]
            if not terms:
                return []

            cypher = """
                WITH $terms AS terms, $applicant_id AS aid
                MATCH (a:Applicant)
                WITH a, terms, aid
                WHERE (aid IS NOT NULL AND a.applicant_id = aid) OR
                      any(term IN terms WHERE
                      toLower(a.applicant_name) CONTAINS term OR
                      toLower(a.policy_type) CONTAINS term OR
                      toLower(a.location) CONTAINS term OR
                      toLower(a.risk_tier) CONTAINS term OR
                      (a.contact_email IS NOT NULL AND toLower(a.contact_email) CONTAINS term)
                )
                RETURN DISTINCT a.applicant_id AS applicant_id,
                       a.applicant_name AS name,
                       a.policy_type     AS policy_type,
                       a.location        AS location,
                       a.risk_tier       AS risk_tier,
                       a.contact_email   AS contact_email,
                       a.contact_phone   AS contact_phone
                LIMIT 20
                """
            results = neo4j.run_query(cypher, {"terms": terms, "applicant_id": uuid_match.group(0) if uuid_match else None})
            return results or []

        if not self.applicants:
            return []

        if uuid_match:
            aid = uuid_match.group(0)
            a = self.applicants.get(aid)
            if a:
                return [{
                    "applicant_id": a.applicant_id,
                    "name": a.name,
                    "policy_type": a.policy_type,
                    "location": a.location,
                    "risk_tier": a.risk_tier,
                    "contact_email": a.contact_email,
                    "contact_phone": a.contact_phone,
                }]
            return []

        q = query.lower()
        matches = list(self.applicants.values())

        locations = {a.location.lower() for a in self.applicants.values()}
        for loc in locations:
            if loc in q:
                matches = [a for a in matches if a.location.lower() == loc]
                break

        policy_types = {a.policy_type.lower() for a in self.applicants.values()}
        for pt in policy_types:
            if pt in q:
                matches = [a for a in matches if a.policy_type.lower() == pt]
                break

        return [
            {
                "applicant_id": a.applicant_id,
                "name": a.name,
                "policy_type": a.policy_type,
                "location": a.location,
                "risk_tier": a.risk_tier,
                "contact_email": a.contact_email,
                "contact_phone": a.contact_phone,
            }
            for a in matches
        ]

    def _db_query_applicant_graph_raw(self, query: str, applicant_service: Any) -> List[Dict[str, Any]]:
        if not query or not isinstance(query, str):
            return []

        uuid_match = self._UUID_RE.search(query)
        applicants = applicant_service.list_all(limit=1000)
        terms = [t for t in re.split(r"\W+", query.lower()) if len(t) >= 2]
        matches = []

        if uuid_match:
            aid = uuid_match.group(0)
            applicant = applicant_service.get_by_id(aid)
            if applicant:
                matches.append({
                    "applicant_id": applicant.applicant_id,
                    "name": getattr(applicant, "applicant_name", None) or applicant.applicant_id,
                    "policy_type": getattr(applicant, "policy_type", None),
                    "location": self._applicant_location_from_applicant(applicant),
                    "risk_tier": getattr(applicant, "metadata", {}).get("risk_tier", "unknown") if isinstance(getattr(applicant, "metadata", {}), dict) else "unknown",
                    "contact_email": None,
                    "contact_phone": None,
                })
                return matches

        for applicant in applicants:
            if self._applicant_matches_query(applicant, terms):
                contact_info = getattr(applicant, "contact_info", None) or {}
                contact_email = None
                contact_phone = None
                if isinstance(contact_info, dict):
                    contact_email = contact_info.get("email")
                    contact_phone = contact_info.get("phone")
                else:
                    contact_email = getattr(contact_info, "email", None)
                    contact_phone = getattr(contact_info, "phone", None)

                matches.append({
                    "applicant_id": applicant.applicant_id,
                    "name": getattr(applicant, "applicant_name", None) or applicant.applicant_id,
                    "policy_type": getattr(applicant, "policy_type", None),
                    "location": self._applicant_location_from_applicant(applicant),
                    "risk_tier": getattr(applicant, "metadata", {}).get("risk_tier", "unknown") if isinstance(getattr(applicant, "metadata", {}), dict) else "unknown",
                    "contact_email": contact_email,
                    "contact_phone": contact_phone,
                })
        return matches

    def query_applicant_graph(self, query: str, applicant_service: Any = None) -> str:
        """Keyword search across applicant name, policy type, location, risk tier, contact info.

        Tier 1 — Neo4j:        full Cypher CONTAINS match, LIMIT 20
        Tier 2 — DynamoDB:     applicant_repo.list_all(1000) + Python filter
        Tier 3 — In-memory:    startup snapshot keyword scan (stale)
        """
        if not query or not isinstance(query, str):
            return "query must be a non-empty string"

        results = self.query_applicant_graph_raw(query, applicant_service)
        return self._format_applicant_query_results(query, results)

    def kg_query_similar(self, applicant_id: str, limit: int = 20) -> str:
        """Return applicants similar to *applicant_id* by policy type and location.

        Response includes: applicant_id, contact info (email, phone), location.
        Excludes: coverage details, premium quotes.
        """
        def _format(results):
            if not results:
                return f"similar_count: 0\napplicant_id: {applicant_id}"
            lines = [f"similar_count: {len(results)}", f"applicant_id: {applicant_id}"]
            for i, r in enumerate(results, 1):
                aid      = r.get("applicant_id") or getattr(r, "applicant_id", "")
                contact  = r.get("contact_info") or getattr(r, "contact_info", {})
                location = r.get("location") or getattr(r, "location", "")
                email    = contact.get("email") if isinstance(contact, dict) else getattr(contact, "email", None)
                phone    = contact.get("phone") if isinstance(contact, dict) else getattr(contact, "phone", None)
                lines.append(f"result {i}:")
                lines.append(f"  applicant_id: {aid}")
                if email:     lines.append(f"  email: {email}")
                if phone:     lines.append(f"  phone: {phone}")
                if location:  lines.append(f"  location: {location}")
            return "\n".join(lines)

        neo4j = get_neo4j_client()
        if neo4j.is_connected():
            return _format(neo4j.find_similar_applicants(applicant_id, limit))

        similar = self.find_similar_applicants(applicant_id, limit)
        return _format([
            {"applicant_id": a.applicant_id,
             "contact_info": {"email": getattr(a, "contact_email", None), "phone": getattr(a, "contact_phone", None)},
             "location": a.location}
            for a, s in (similar or [])
        ])

    def kg_detect_risk_cluster(self, applicant_id: str) -> str:
        """Return similar applicants based on policy type and location."""
        neo4j = get_neo4j_client()
        if neo4j.is_connected():
            ctx = neo4j.get_applicant_context(applicant_id)
            if not ctx:
                return f"No data found for applicant {applicant_id}."
            similar = ctx.get("similar_applicants") or []
            similar = [c for c in similar if c.get("id")]
            lines = [
                f"  - {c.get('name', c['id'])} (id={c['id']})"
                for c in similar
            ]
            summary = (
                f"Applicant: {ctx.get('name', applicant_id)} (id={applicant_id})\n"
                f"Policy Type: {ctx.get('policy_type', '?')}\n"
                f"Risk Tier: {ctx.get('risk_tier', '?')} (segment-based proxy — not an actual underwriting score)\n"
                f"Similar applicants ({len(lines)}):\n"
                + ("\n".join(lines) if lines else "  none")
            )
            return summary

        peers = self.get_related_applicants(applicant_id)
        if not peers:
            return f"No connected applicants found for {applicant_id} (Neo4j disabled)."
        lines = [f"  - {a.name} (id={a.applicant_id})" for a in peers]
        return f"Connected applicants for {applicant_id} ({len(lines)}):\n" + "\n".join(lines)

    def kg_explain_applicant(
        self,
        applicant_id: str,
        applicant_service: Any = None,
        policy_service: Any = None,
    ) -> str:
        """Return a raw applicant context summary for *applicant_id* for agent reasoning.

        When applicant_service is provided the full domain object is fetched from
        the database and serialized for agent interpretation.
        Falls back to the Neo4j context graph, then to in-memory data.
        """
        # ── Tier 1: raw domain serialization ──────────────────────────────
        if applicant_service is not None:
            try:
                applicant = applicant_service.get_by_id(applicant_id)
                if applicant:
                    from mltek.common.tools.format import format_json_as_text
                    data = applicant.model_dump(mode="json", exclude_none=True) if hasattr(applicant, "model_dump") else {}
                    if policy_service is not None:
                        try:
                            policies = policy_service.list_by_applicant(applicant_id) or []
                            if policies:
                                data["policies"] = [p.model_dump(mode="json", exclude_none=True) if hasattr(p, "model_dump") else {} for p in policies]
                        except Exception:
                            pass
                    return format_json_as_text(data)
            except Exception:
                pass

        # ── Tier 2: Neo4j context graph ────────────────────────────────────
        neo4j = get_neo4j_client()
        if neo4j.is_connected():
            ctx = neo4j.get_applicant_context(applicant_id)
            if not ctx:
                return (
                    f"applicant_id: {applicant_id}\n"
                    f"status: not found\n"
                    f"message: Applicant not found in knowledge graph."
                )
            similar = [p for p in (ctx.get("similar_applicants") or []) if p.get("id")]
            similar_names = ", ".join(p.get("name", p["id"]) for p in similar[:5])
            if len(similar) > 5:
                similar_names += f" (+{len(similar) - 5} more)"
            return (
                f"Applicant:  {ctx.get('name', applicant_id)} (id={applicant_id})\n"
                f"Policy:     {ctx.get('policy_type', '?')}\n"
                f"Location:   {ctx.get('location', '?')}\n"
                f"Risk Tier:  {ctx.get('risk_tier', '?')} (segment-based proxy — not an actual underwriting score)\n"
                f"Similar ({len(similar)}): {similar_names or 'none'}"
            )

        # ── Tier 3: in-memory fallback ─────────────────────────────────────
        node = self.get_applicant(applicant_id)
        if not node:
            return (
                f"applicant_id: {applicant_id}\n"
                f"status: not found\n"
                f"message: Applicant not found in knowledge graph."
            )
        similar = self.find_similar_applicants(applicant_id, 3)
        sim_str = ", ".join(a.name for a, _ in similar) if similar else "none"
        return (
            f"Applicant: {node.name} (id={applicant_id})\n"
            f"Policy:    {node.policy_type}\n"
            f"Location:  {node.location}\n"
            f"Similar:   {sim_str}\n"
            "(Neo4j disabled — extended policy data unavailable)"
        )

    def load(self) -> None:
        if not self.data_file or not self.data_file.exists():
            return
        try:
            with open(self.data_file, "r") as f:
                data = json.load(f)
            for aid, applicant_data in data.get("applicants", {}).items():
                self.applicants[aid] = ApplicantNode.from_dict(applicant_data)
            for rid, rel_data in data.get("relationships", {}).items():
                self.relationships[rid] = ApplicantRelationship.from_dict(rel_data)
            for rel in self.relationships.values():
                if rel.source_id in self.applicants:
                    self.applicants[rel.source_id].add_relationship(rel)
        except Exception as e:
            logger.debug(f"Error loading knowledge graph: {e}")


