# Neo4j Graph Database Integration

This guide explains how to set up and use Neo4j for hierarchical and relationship analytics in the Paytek system.

## Overview

Neo4j is used to complement PostgreSQL for efficient multi-hop traversals and graph-based analytics:

- **PostgreSQL**: Handles transactional operations (CRUD, reporting)
- **Neo4j**: Handles relationship queries and graph analytics

This hybrid approach avoids expensive recursive queries in SQL while keeping transactional integrity.

## Setup

### 1. Install and Start Neo4j

#### Using Docker (Recommended)

```bash
docker run -d \
  --name neo4j \
  -p 7474:7474 \
  -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/neo4j_password \
  neo4j:latest
```

#### Using Homebrew (macOS)

```bash
brew install neo4j
neo4j console
```

#### Using APT (Linux)

```bash
sudo apt-get install neo4j
sudo systemctl start neo4j
```

### 2. Configure Environment Variables

Add to `.env` file:

```bash
# Neo4j Configuration
NEO4J_ENABLED=true
NEO4J_HOST=localhost
NEO4J_PORT=7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=neo4j_password
NEO4J_DATABASE=neo4j
```

### 3. Verify Connection

```python
from db.graph import get_neo4j_client

client = get_neo4j_client()
if client.is_connected():
    print("Connected to Neo4j!")
else:
    print("Neo4j not available")
```

## Usage

### Basic Operations

#### 1. Create Group Nodes

```python
from db.graph import get_neo4j_client

neo4j = get_neo4j_client()

# Create a group
neo4j.create_group_node(
    group_id="grp_123",
    group_name="Enterprise Merchants",
    parent_group_id=None  # Root group
)

# Create child group
neo4j.create_group_node(
    group_id="grp_124",
    group_name="Premium Enterprise",
    parent_group_id="grp_123"  # Child of grp_123
)
```

#### 2. Add Merchants to Groups

```python
neo4j.create_merchant_in_group(
    group_id="grp_123",
    merchant_id="mrch_456",
    merchant_name="Acme Corp"
)
```

#### 3. Query Hierarchies

```python
# Get all descendants (children, grandchildren, etc.)
descendants = neo4j.get_all_descendants("grp_123")
print(f"Found {len(descendants)} descendants")

# Get all ancestors (parents, grandparents, up to root)
ancestors = neo4j.get_all_ancestors("grp_124")
print(f"Found {len(ancestors)} ancestors")
```

### Advanced Queries

#### Find Connected Merchants

```python
from db.graph import get_graph_hierarchy_service

service = get_graph_hierarchy_service()

# Find merchants connected through 2 hops of relationships
connected = service.find_connected_merchants("mrch_456", hops=2)
print(f"Found {len(connected)} connected merchants")
```

#### Detect Merchant Clusters

```python
clusters = service.detect_merchant_clusters()
for cluster in clusters:
    print(f"Cluster {cluster['cluster_name']}: {cluster['merchant_count']} merchants")
```

#### Network Distance Analysis

```python
distance = service.analyze_network_distance("mrch_456", "mrch_789")
if distance:
    print(f"Merchants are {distance} hops apart")
else:
    print("Merchants not connected")
```

### Synchronization

Keep Neo4j synchronized with PostgreSQL changes:

```python
from db.graph.graph_sync import get_graph_sync

sync = get_graph_sync()

# Sync when creating a group
group_id = create_group_in_postgres(...)
sync.sync_on_group_create(group_id, "Group Name", parent_id)

# Sync when adding merchant to group
sync.sync_on_merchant_added_to_group(group_id, merchant_id, merchant_name)

# Full sync of hierarchy
sync.sync_group_hierarchy()
```

## MCP Tools Available

When Neo4j is enabled, additional graph-based tools are available:

- `get_group_descendants_graph` - Get descendants from graph
- `get_group_ancestors_graph` - Get ancestors from graph
- `find_connected_merchants_graph` - Find merchants through relationships
- `detect_merchant_clusters_graph` - Detect merchant clusters
- `analyze_network_distance_graph` - Analyze merchant connectivity

## Performance Characteristics

### Query Type Comparison

| Query | PostgreSQL | Neo4j |
|-------|-----------|-------|
| Direct lookup | ⚡ <10ms | ⚡ <10ms |
| 1-2 hop traversal | ⚡ 10-50ms | ⚡ 5-20ms |
| 5-10 hop traversal | 🐢 500ms+ | ⚡ 20-50ms |
| Pattern matching | 🐢 Slow | ✨ Fast |
| Community detection | ❌ Hard | ✨ Built-in |

### When to Use Neo4j

✅ Use Neo4j for:
- Deep hierarchical traversals (5+ levels)
- Finding merchants connected through multiple groups
- Fraud pattern detection
- Network clustering
- Recommendation systems
- Real-time relationship analysis

❌ Use PostgreSQL for:
- Creating/updating merchant data
- Financial transactions
- Compliance reporting
- Operational queries with strong consistency

## Troubleshooting

### Connection Issues

```python
from db.graph import get_neo4j_client

client = get_neo4j_client()
print(f"Connected: {client.is_connected()}")
print(f"Config: {client.config}")
```

### Sync Issues

```python
from db.graph.graph_sync import get_graph_sync

sync = get_graph_sync()
success = sync.sync_group_hierarchy()
if not success:
    print("Sync failed - check Neo4j connection")
```

### Query Performance

Monitor query performance:

```bash
# In Neo4j Browser (http://localhost:7474)
PROFILE MATCH (g:Group {group_id: 'grp_123'})<-[:HAS_PARENT*]-(desc:Group) RETURN desc
```

## Cypher Reference

Common Cypher patterns used in the system:

```cypher
-- Get all descendants
MATCH (root:Group {group_id: 'grp_123'})<-[:HAS_PARENT*]-(descendants:Group)
RETURN descendants

-- Get all ancestors
MATCH (child:Group {group_id: 'grp_124'})-[:HAS_PARENT*]->(ancestors:Group)
RETURN ancestors

-- Find connected merchants
MATCH (m1:Merchant {merchant_id: 'mrch_456'})-[:MEMBER_OF|HAS_PARENT*]-(m2:Merchant)
RETURN DISTINCT m2

-- Get shortest path
MATCH p = shortestPath((m1:Merchant {merchant_id: 'mrch_456'})-[:MEMBER_OF|HAS_PARENT*]-(m2:Merchant {merchant_id: 'mrch_789'}))
RETURN length(p) as distance
```

## Best Practices

1. **Regular Sync**: Sync periodically after batch operations
2. **Connection Pooling**: Neo4jClient uses connection pooling (max 50 connections)
3. **Query Timeouts**: Set reasonable timeouts for long-running queries
4. **Backups**: Regular Neo4j backups for data protection
5. **Monitoring**: Monitor Neo4j memory usage for large graphs

## Advanced Configuration

### Custom Query Timeout

```python
# In neo4j_client.py, modify run_query():
query_config = {"timeout": 30}  # 30 second timeout
session.run(query, parameters, query_config)
```

### Connection Pool Size

```python
# In neo4j_client.py, modify _connect():
self.driver = GraphDatabase.driver(
    uri,
    auth=(user, password),
    database=database,
    max_pool_size=100  # Increase if needed
)
```

## Migration Guide

### Migrating from PostgreSQL-Only Setup

1. **Enable Neo4j**: Set `NEO4J_ENABLED=true` in `.env`
2. **Initial Sync**: Run `sync_group_hierarchy()` to populate Neo4j
3. **Test Queries**: Run graph queries in parallel to validate
4. **Gradual Rollout**: Enable graph tools for specific use cases first
5. **Monitor**: Watch Neo4j performance metrics

## Support

For issues or questions:
1. Check Neo4j logs: `docker logs neo4j`
2. Test connection: `cypher-shell -a neo4j://localhost:7687`
3. Review query plans in Neo4j Browser
4. Check memory usage and adjust if needed
