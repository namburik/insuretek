---
name: health-check-diagnostic
description: Diagnose PayTek service health issues and suggest remediation
category: operations
applyTo: ["paytek"]
supportedActions: ["diagnose", "remediate", "analyze"]
---

# Health Check Diagnostic Skill

**Purpose:** Quickly diagnose and resolve PayTek service health issues across all critical dependencies (Ollama, MCP Server, PostgreSQL, DynamoDB, Neo4j).

**When to Use:**
- Agent calls are hanging or failing
- User reports: "Why isn't the agent responding?"
- After infrastructure changes
- Before deploying new features
- Debugging correlation ID failures
- Investigating circuit breaker state

**Invocation:** `diagnose paytek health` or `why is [component] failing?`

---

## What The Skill Does

### 1. **Immediate Diagnostics**
```
$ diagnose paytek health

SERVICE HEALTH REPORT
======================================================================
✓ Ollama LLM: Connected to qwen3.5:2b [250ms]
  Model loaded, inference ready

✗ MCP Server: Cannot reach http://127.0.0.1:8091/mcp_core
  Connection refused - service not running
  Last attempt: 2026-05-05 14:32:14 (2 minutes ago)

✓ PostgreSQL Database: Connected to localhost:5432/mltek [180ms]
  7 tables, last query: 14:31:45

⚠ DynamoDB: Connected but slow (1200ms)
  Endpoint: http://localhost:8082
  Recommendation: Check local Docker performance

⚠ Neo4j: Neo4j disabled in configuration
  To enable: Set NEO4J_ENABLED=true in .env
```

### 2. **Root Cause Analysis**
Correlates health issues with:
- Recent agent failures
- Correlation IDs from failed requests
- Environmental changes (recent commits, config changes)
- Service dependencies

```
Root Cause Analysis:
  MCP Service Down
    ├─ Since: 14:32:10 (matches circuit breaker open)
    ├─ Correlation IDs affected: ABC-123, XYZ-789, DEF-456
    ├─ Requests failed: 47 (4 retries each = 188 total attempts)
    ├─ Last 5 requests: merchant-list, platform-search, group-query
    └─ Duration: 3 minutes 42 seconds
```

### 3. **Smart Remediation**
Generates specific, runnable fixes:

```
RECOMMENDED FIXES (in order):

1. START MCP SERVER (immediate impact)
   $ python3 src/mcp_server/server.py --port 8091 --log-level INFO
   Expected recovery time: <5 seconds
   Affected services: 5 tools will resume
   
2. VERIFY OLLAMA (if MCP startup fails)
   $ curl http://localhost:11434/api/tags
   Expected: {"models": [{"name": "qwen3.5:2b"}]}
   
3. CHECK POSTGRESQL CONNECTION
   $ psql -h localhost -U namburi -d mltek -c "SELECT 1"
   Current status: ✓ HEALTHY
   
4. OPTIONAL: Monitor with health watch
   $ python check_health.py --watch --interval 2
```

### 4. **Impact Analysis**
Shows what agents/tools are affected:

```
AFFECTED COMPONENTS:
  ✗ BLOCKING (agents fail immediately):
    • run_autogen_agent() - Tool execution blocked
    • call_supervisor_agent() - Cannot delegate to admin
    • get_merchant_platforms() - MCP call required
    • find_merchants_by_platform() - MCP call required
  
  ⚠ DEGRADED (fallback responses):
    • get_all_merchants_with_platforms() - Returns empty list
    • get_high_value_merchants() - Cached data (1 hour old)
    • get_geographic_distribution() - Cached data (1 hour old)
  
  ✓ UNAFFECTED:
    • response_evaluation - Local processing
    • health_checks - Diagnostic tools
    • logging - File operations
```

### 5. **Detailed Logs for Debugging**
Extracts relevant logs for correlation_id:

```
CORRELATION TRACE:
  Request ID: ABC-123
  Timeline:
    14:32:10.123 [ABC-123] Agent started
    14:32:10.145 [ABC-123] Health check: MCP HEALTHY
    14:32:10.523 [ABC-123] MCP call attempted
    14:32:10.987 [ABC-123] TIMEOUT (5s) - No response from MCP
    14:32:11.045 [ABC-123] Retry 1/3
    14:32:11.523 [ABC-123] Circuit breaker: CLOSED (2/5 failures)
    14:32:12.123 [ABC-123] TIMEOUT - Retry 2/3
    14:32:13.523 [ABC-123] TIMEOUT - Retry 3/3
    14:32:14.045 [ABC-123] Circuit breaker: OPEN (5/5 failures)
    14:32:14.134 [ABC-123] Returning fallback response
    14:32:14.152 [ABC-123] Request failed (total time: 4.0s)
```

---

## How It Works

### Step 1: Health Check Execution
```python
# Runs check_all_services() from common/health_check.py
services = await check_all_services(critical_only=True)
```

### Step 2: Service Status Analysis
- **HEALTHY**: Response time <500ms, no errors
- **DEGRADED**: Response time >500ms or partial failures
- **UNHEALTHY**: Timeout or connection refused

### Step 3: Attempt Recovery (If Enabled)
```python
# Try to recover unhealthy services (safe services only)
if any_service_unhealthy:
    recovery_results = await attempt_service_recovery()
    # Auto-restarts: MCP Server
    # Manual required: Ollama, PostgreSQL, DynamoDB, Neo4j
    
    if recovery_succeeded:
        # Continue with agent execution
        pass
```

### Step 4: Correlation Matching
```python
# Find correlation IDs from recent failures
failures = grep_logs(r"TIMEOUT|CONNECTION|FAILED", 
                     since="5 minutes ago")
# Extract correlation_ids from failures
affected_requests = extract_correlation_ids(failures)
```

### Step 5: Impact Assessment
```python
# Map service → affected agents/tools
impact = {
    "mcp_server": ["run_autogen_agent", "call_supervisor_agent", ...],
    "ollama": ["all_agents"],
    "postgresql": ["service_layer"],
}
```

### Step 6: Remediation Generation
```python
# Based on service status and recovery results, suggest fixes
fixes = []
for service_name, recovery_result in recovery_results.items():
    if recovery_result.success:
        fixes.append(f"✓ {service_name} recovered automatically")
    else:
        fixes.append(get_manual_fix_command(service_name))
```

---

## Service Recovery Integration

The skill works with `common/resilience.py` for automatic service recovery:

### Recoverable Services

| Service | Auto-Recover? | Why |
|---------|---------------|-----|
| **MCP Server** | ✓ YES | Stateless, safe to restart |
| **Ollama** | ✗ NO | Docker-based, complex |
| **PostgreSQL** | ✗ NO | Data integrity risk |
| **DynamoDB** | ✗ NO | Data loss risk |
| **Neo4j** | ✗ NO | Graph state risk |

### Recovery Flow

```
Agent detects unhealthy service
  ↓
Calls: attempt_service_recovery()
  ↓
For MCP Server:
  • Start subprocess: python mcp_server/server.py --port 8091
  • Wait 2 seconds for startup
  • Health check: Verify listening
  ↓
For other services:
  • Log as requiring manual action
  • Provide remediation command
  ↓
Verify: verify_recovery_succeeded()
  ↓
If recovered: Continue agent execution (no hang!)
If failed: Return error with suggestions
```

### Example: MCP Server Auto-Recovery

```
User: "List all merchants"
  ↓
Agent: Health check → MCP Server is DOWN
  ↓
Agent: Starts recovery process
    14:32:14 Attempting MCP Server recovery...
    14:32:14 Starting subprocess (python mcp_server/server.py)
    14:32:16 Waiting for startup (PID: 12345)
    14:32:18 Verify health check
    14:32:18 ✓ MCP Server healthy (250ms latency)
  ↓
Agent: Continues execution (no user intervention!)
  ↓
Result: "Merchant list returned (request time: 3.2s)"
```

---

## Integration Points

### With Correlation IDs
```
User: "Request ABC-123 is failing"
  ↓
Skill finds: ABC-123 correlation_id in logs
  ↓
Traces: All events during that request
  ↓
Identifies: MCP was down at 14:32:14 (2 min timeout)
```

### With LangSmith
```
Query LangSmith for correlation_id=ABC-123
  ↓
Extract: All spans during that request
  ↓
Correlate: With health check failures at same time
```

### With Health Check Module
```
Calls: common/health_check.py check_all_services()
  ↓
Gets: Latency, error messages, service details
  ↓
Displays: With timestamps and context
```

---

## Example Queries

### Basic Diagnosis
```
"Why is the agent hanging?"
"What's wrong with the system?"
"Diagnose paytek health"
```

### Specific Service
```
"Is MCP Server running?"
"Check database connectivity"
"Why is Ollama slow?"
```

### Request Debugging
```
"What happened with request ABC-123?"
"Why did this merchant query timeout?"
"Trace correlation_id XYZ-789"
```

### Remediation
```
"How do I fix the MCP Server?"
"What should I do about the circuit breaker being open?"
"Show me the fix for the Ollama timeout"
```

---

## Output Format

### For Automated Systems
```json
{
  "status": "UNHEALTHY",
  "timestamp": "2026-05-05T14:32:14Z",
  "services": {
    "ollama": {"status": "HEALTHY", "latency_ms": 250},
    "mcp_server": {"status": "UNHEALTHY", "error": "Connection refused"},
    "postgresql": {"status": "HEALTHY", "latency_ms": 180},
    "dynamodb": {"status": "DEGRADED", "latency_ms": 1200},
    "neo4j": {"status": "DISABLED"}
  },
  "affected_requests": ["ABC-123", "XYZ-789"],
  "recommendations": [
    {"priority": 1, "action": "start_mcp_server", "command": "python ..."},
    {"priority": 2, "action": "check_ollama", "command": "curl ..."}
  ]
}
```

### For User Display
```
🔴 SYSTEM UNHEALTHY - 1 critical service down

IMMEDIATE ACTION REQUIRED:
  MCP Server is not responding
  Start with: python mcp_server/server.py --port 8091
  Recovery time: <5 seconds

AFFECTED OPERATIONS:
  • Agent calls: BLOCKED
  • Merchant queries: BLOCKED
  • Platform operations: BLOCKED

CORRELATION: 47 requests failed since 14:32:10
```

---

## Configuration

Control behavior via environment variables:

```env
# Health Check Settings
HEALTH_CHECK_ON_AGENT_START=true        # Run before each agent call
HEALTH_CHECK_TIMEOUT=5                  # Max seconds per service check
HEALTH_CHECK_LOG_LEVEL=INFO             # Verbosity

# Diagnostics Settings
DIAG_INCLUDE_LOGS=true                  # Include recent logs
DIAG_LOOK_BACK_MINUTES=10               # How far back to search logs
DIAG_VERBOSE=false                      # Detailed output
```

---

## Related Files

- `common/health_check.py` - Core service checks
- `check_health.py` - CLI tool (wraps health check module)
- `common/context.py` - Correlation ID management
- `common/logging_config.py` - Structured logging with filters
- `ARCHITECTURE_REVIEW.md` - System design documentation

---

## Limitations & Future Work

### Current Limitations
- Health checks are point-in-time (no historical trending)
- Assumes local services (no cloud service support yet)
- Limited to pre-defined service list

### Future Enhancements
- **Health history:** Track service uptime over time
- **Predictive alerts:** Warn before service goes down
- **Auto-remediation:** Automatically start failed services
- **Cloud support:** Check AWS/GCP/Azure services
- **Custom health checks:** User-defined service checks
- **Metrics export:** Send to Prometheus/Datadog

---

## Quick Reference

| Issue | Command | Fix |
|-------|---------|-----|
| Agent hangs | `diagnose paytek health` | Start MCP or Ollama |
| Circuit breaker open | `diagnose paytek health` | Wait 60s or restart MCP |
| Slow queries | `diagnose paytek health` | Check database latency |
| Correlation trace | `trace ABC-123` | Shows full request timeline |
| See logs | `logs correlation=ABC-123` | Grep logs by request ID |

---

**Last Updated:** 2026-05-05  
**Version:** 1.0  
**Skill Owner:** PayTek Operations Team
