// Merchant vector index — Neo4j 5.11+ HNSW (cosine similarity)
//
// This index is provisioned automatically by Neo4jClient._setup_vector_index()
// on every startup (CREATE ... IF NOT EXISTS). Run manually only for recovery
// or when bootstrapping a fresh database before the service starts.
//
// Embedding dimension (768) matches:
//   - nomic-embed-text:latest  (Ollama, default)
//   - text-embedding-004        (Google, MODEL_PROVIDER=google)
//
// Override with PGVECTOR_EMBEDDING_DIM env var if switching models.

CREATE VECTOR INDEX `applicant-embedding` IF NOT EXISTS
FOR (m:Merchant) ON (m.embedding)
OPTIONS {indexConfig: {
    `vector.dimensions`: 768,
    `vector.similarity_function`: 'cosine'
}};

// Verify index is online before querying:
// SHOW INDEXES WHERE name = 'applicant-embedding';

// Example: nearest-neighbour search
// CALL db.index.vector.queryNodes('applicant-embedding', 10, $embedding)
// YIELD node AS m, score
// RETURN m.merchant_id, score
// ORDER BY score DESC;
