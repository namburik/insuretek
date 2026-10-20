# Insuretek

Insuretek automates applicant onboarding for insurance products by combining
conversational intake, policy-driven validation, and workflow orchestration
with semantic decision support.

For the full architecture, component design, and policy details, see
[design/Design.md](design/Design.md).

## Package structure

| Package | Role |
|---|---|
| `mltek` | Control layer — framework, policies, routing, agent execution |
| `insuretek` | Domain layer — implements the `mltek` domain provider interface |
| `core_data` | Data access layer — repositories, DB models, audit persistence |
| `bff_service` | Intake layer — conversational session handling, prompt validation |
| `mcp_server` | MCP tool server — exposes data tools to agents |
| `workflow_engine` | Workflow state machine and orchestration |
| `web_gateway` | HTTP layer — routes, React frontend |

## Requirements

- Python >= 3.11
- [uv](https://github.com/astral-sh/uv) (or pip) for dependency management
- Node.js/npm (for the React frontend under `src/web_gateway/react`)
- Docker, for running DynamoDB Local and the MCP services via `docker-compose.yml`

## Setup

```bash
uv sync
# or: pip install -r requirements.txt
```

## Running locally

```bash
./run.ksh
```

This builds the React UI and starts the MCP servers and web gateway on
`http://localhost:6001`.

Alternatively, run everything via Docker Compose:

```bash
docker-compose up --build
```

## Testing

```bash
pytest
```
