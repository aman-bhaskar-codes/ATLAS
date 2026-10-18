# ATLAS

**Autonomous Task & Learning Agent System**

ATLAS is an open-source, local-first autonomous agent runtime implemented in Python and accompanied by a Next.js web dashboard. It unifies task orchestration, multi-provider model routing, five-tier cognitive memory, document retrieval, and sandboxed developer workspace tools into a single, safety-governed architecture.

The repository investigates how autonomous reasoning agents can operate within strict execution controls—enforcing deterministic risk classification, human approval gates, forward-linked cryptographic audit records, and reproducible quality gates across CLI, HTTP, and WebSocket interfaces.

---

## Architecture and Execution Model

Every interaction—whether initiated via the CLI, HTTP REST endpoints, WebSockets, speech, or recurring scheduled automations—is ingested into a unified execution pipeline:

```text
                      ┌──────────────────────────────────────────────┐
                      │    Ingress: CLI / REST / WebSocket / Cron    │
                      └──────────────────────┬───────────────────────┘
                                             │ InboundEvent
                                             ▼
                      ┌──────────────────────────────────────────────┐
                      │       Task Orchestration (OTAR Engine)       │
                      │  Bounded Reasoning: Observe-Think-Act-Reflect │
                      │  Memory Context Injection & Tool Selection   │
                      └──────────────────────┬───────────────────────┘
                                             │ ToolRequest
                                             ▼
                      ┌──────────────────────────────────────────────┐
                      │          Safety Classifier & Engine          │
                      │  Risk Tiers (T0–T4) & Policy Enforcement     │
                      └──────────────┬────────────────┬──────────────┘
                        Allowed / Approved          Denied
                                     │                │
                                     ▼                ▼
                      ┌──────────────────────┐ ┌─────────────────────┐
                      │    Tool Execution    │ │  Rejection Notice   │
                      │   Docker / Native    │ │  Terminal / Event   │
                      └──────────────┬───────┘ └──────┬──────────────┘
                                     │                │
                                     └────────┬───────┘
                                              │
                                              ▼
                      ┌──────────────────────────────────────────────┐
                      │      Forward-Linked Cryptographic Audit      │
                      │     SHA-256 Hash Chaining in SQLite Database │
                      └──────────────────────────────────────────────┘
```

### Request Lifecycle

1. **Ingress Normalization**: All incoming requests are converted into an immutable `InboundEvent` with source metadata and provenance tracking. Transports cannot bypass execution controls.
2. **Orchestrator Cycle (OTAR)**: The runtime executes an Observe-Think-Act-Reflect loop. Active context is assembled from working memory, episodic recollections, and knowledge retrieval before delegating to the configured model.
3. **Deterministic Safety Classification**: Proposed tool invocations are mapped to a five-tier risk taxonomy. Classification is fail-closed: unrecognized operations default to requiring human confirmation.
4. **Policy Evaluation & Approval**:
   - **T0 / T1**: Executed immediately or with asynchronous notification.
   - **T2 / T3**: Suspended into the human approval queue, requiring interactive CLI confirmation or dashboard review (T3 requires an explicit approval code).
   - **T4**: Terminated immediately by policy.
   - **Kill Switch**: Re-verified immediately prior to dispatch to respect concurrent stop commands.
5. **Tool Execution**: Tools execute within a resource-capped Docker container (`python:3.13-slim`) or via the host-level native executor for local development.
6. **Cryptographic Audit Log**: Execution records, inputs, hashes, and timestamps are recorded in an append-only SQLite audit log forward-linked by SHA-256 hash chains.

---

## Subsystem Overview

### 1. Task Orchestration
- **Loop Bounds**: Enforces maximum execution steps (default: 25), per-step timeouts (default: 120s), and cumulative task timeouts (default: 1800s).
- **Execution Leases**: Heartbeat-monitored runtime execution leases allow detection and recovery of abandoned or crashed tasks.
- **Task Trajectories**: Captures structured reasoning steps, model inputs, tool arguments, observations, and error reflections into persisted trajectory datasets for offline inspection.

### 2. Five-Tier Cognitive Memory
- **Working Memory**: Manages active task state, scratchpad thoughts, and running conversation context with token-budget truncation.
- **Episodic Memory**: Stores historical task records, outcomes, and failure reflections for retrospective lookup.
- **Semantic Memory**: Stores extracted entities, relationships, and structured domain facts.
- **User Profile**: Tracks operator preferences, environmental constraints, and approved working directories.
- **Dual-Lane Retrieval**: Combines dense semantic search (ChromaDB with local `bge-m3:latest` embeddings) and lexical keyword matching (BM25) fused via Reciprocal Rank Fusion (RRF, $k=60$) and feature-based reranking.

### 3. Safety & Policy Engine
- **Risk Taxonomy**:
  - `Tier 0 (Auto)`: Read-only, non-destructive inspection (e.g., file reading, directory listing, system diagnostics).
  - `Tier 1 (Notify)`: Low-risk operations or informational search queries.
  - `Tier 2 (Confirm)`: State mutations requiring explicit operator approval (e.g., file modifications, dependency updates).
  - `Tier 3 (Confirm Code)`: High-risk mutations (e.g., file overwrites, code execution, destructive Git commands) requiring approval code entry.
  - `Tier 4 (Deny)`: Hard-blocked patterns (e.g., credential access, unauthorized system paths, destructive disk utilities).
- **Audit Records**: Forward-linked hash chain using SHA-256 linking `(prior_hash, timestamp, action, payload)` to ensure audit continuity.

### 4. Model Routing & Budget Gateway
- **Multi-Provider Fleet**: Abstraction supporting local Ollama models, Google Gemini, and OpenRouter endpoints.
- **Profiles**: Configurable profiles (`local_free`, `hybrid`, `performance`) to control provider selection and avoid unintended paid route invocation.
- **Rate & Cost Guards**: Token bucket rate limiters, per-request token ceilings, and daily/monthly budget tracking.

### 5. Developer Workspaces & Tooling
- **Tool Registry**: Catalog with Pydantic schema validation, permission annotations, and dynamic discovery.
- **Execution Sandboxing**: Docker container runner with configurable CPU and memory limits. Development host executor available for local debugging.
- **Workspace Tools**: Directory navigation, file editing with Myers shortest edit script (SES) diff previews, syntax checking, and Git status/commit operations.
- **Model Context Protocol (MCP)**: Implements MCP client support over `stdio` and `sse` transports with automated tool discovery and argument mapping.

---

## Technology Stack

| Domain | Technologies | Details |
| --- | --- | --- |
| **Backend Core** | Python 3.13+, Asyncio, Uvicorn | Async runtime architecture |
| **API & Contracts** | FastAPI, Pydantic v2, Pydantic Settings | Type-safe REST endpoints and schemas |
| **Persistence** | SQLite (`aiosqlite`), PostgreSQL (`asyncpg`) | 36 forward-only schema migrations |
| **Vector & Retrieval** | ChromaDB, BM25, Reciprocal Rank Fusion | Local dense/lexical search pipeline |
| **Frontend Dashboard** | Next.js 16, React 19, TypeScript, Tailwind CSS 4 | App Router, Server Components, Lucide Icons |
| **Client State** | TanStack Query v5, Zod | Type-safe client queries and payload validation |
| **Protocol Support** | Model Context Protocol (MCP) SDK, SSE, WebSockets | Standardized tool and streaming protocols |
| **Tool Integrations** | Playwright, Docker Engine SDK | Web automation and isolated container execution |
| **Verification** | Pytest, Ruff, Mypy, Import-Linter, Vitest | Multi-layer static and dynamic quality gates |
| **Package Management**| `uv`, `npm` | Deterministic lockfiles (`uv.lock`, `package-lock.json`) |

---

## Repository Structure

```text
ATLAS/
├── config/                  # Configuration files (models, permissions, settings, MCP)
├── docs/                    # Technical architecture, startup, routing, and tooling docs
├── eval/                    # Evaluation fixtures and recorded answer benchmarks
├── frontend/                # Next.js 16 / React 19 dashboard application
│   ├── app/                 # App Router pages (chat, tasks, research, ide, settings)
│   ├── components/          # Reusable UI component library
│   └── lib/                 # API client, types, and authentication utilities
├── scripts/                 # Maintenance, evaluation, and database migration scripts
├── src/
│   ├── atlas/               # Core backend library
│   │   ├── adaptation/      # Experimental strategy evaluation and reflection tuning
│   │   ├── bootstrap/       # Service wiring and composition root
│   │   ├── capabilities/    # Browser, workspace, voice, and identity integrations
│   │   ├── infra/           # Persistence, SQLite/Postgres adapters, event bus, leases
│   │   ├── intelligence/    # Model gateway, provider adapters, budget tracking
│   │   ├── interfaces/      # FastAPI API router and WebSocket handlers
│   │   ├── knowledge/       # Document ingestion, OCR, and vector indexing
│   │   ├── memory/          # Working, episodic, semantic, and user profile stores
│   │   ├── orchestration/   # Task loop, OTAR reasoning, planner, and dispatcher
│   │   ├── safety/          # Classifier, approval queue, audit chain, kill switch
│   │   ├── tooling/         # Tool registry, catalog, and MCP client implementation
│   │   ├── tools/           # Built-in tool implementations (filesystem, shell, git)
│   │   └── training/        # Experimental local IDE fine-tuning and triplet pipelines
│   └── atlas_cli/           # Typer CLI application entry point
├── tests/                   # Pytest test suite (unit, integration, safety, e2e)
├── importlinter.ini         # Architectural boundary and layering contracts
└── pyproject.toml           # Python package metadata and tool configurations
```

---

## Local Development Setup

### Prerequisites

- **Python**: Version 3.13 or newer.
- **uv**: Modern Python package and project manager.
- **Node.js**: Version 22 LTS with `npm`.
- **Ollama**: Required for local-only inference (`qwen3:4b` and `bge-m3:latest`).
- **Docker**: (Optional) Required for containerized tool sandboxing.

### 1. Clone and Install Dependencies

```bash
git clone https://github.com/aman-bhaskar-codes/ATLAS.git
cd ATLAS

# Sync backend virtual environment from locked dependencies
uv sync --frozen
```

### 2. Configure Environment

Create an instance configuration file (`.env`) in the repository root:

```dotenv
ATLAS_ENV=dev
ATLAS_DATA_DIR=./.atlas
ATLAS_PROFILE=local_free
ATLAS_OLLAMA_HOST=http://127.0.0.1:11434
ATLAS_LOCAL_MODEL=qwen3:4b
ATLAS_DEFAULT_MODEL=local-qwen3
ATLAS_EMBED_PROVIDER=ollama
ATLAS_EMBED_MODEL=bge-m3:latest
ATLAS_LOCAL_EMBED_MODEL=bge-m3:latest
ATLAS_API_KEYS=
ATLAS_MASTER_KEY=
```

Generate a secure master key for local credential encryption:

```bash
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the generated hex string into `ATLAS_MASTER_KEY`. Pull the local models via Ollama:

```bash
ollama pull qwen3:4b
ollama pull bge-m3:latest
```

### 3. Start Backend API Service

Run Uvicorn via the application factory on loopback port `8730`:

```bash
uv run uvicorn atlas.interfaces.api.app:create_app --factory --host 127.0.0.1 --port 8730
```

Verify runtime health and configuration in a separate terminal:

```bash
uv run python -m atlas.interfaces.cli doctor
```

Interactive OpenAPI documentation is available at `http://127.0.0.1:8730/api/docs`.

### 4. Start Next.js Web Dashboard

```bash
cd frontend
npm ci
npm run dev
```

The web dashboard is served at `http://localhost:3030`. It connects by default to `http://localhost:8730/api/v1`.

---

## Configuration Reference

| Configuration File | Scope & Responsibility |
| --- | --- |
| `config/settings.yaml` | Runtime parameters, inference profiles, memory retention, timeouts |
| `config/models.yaml` | Model fleet registry, context windows, cost models, provider endpoints |
| `config/permissions.yaml` | Operation risk classifications, allowed filesystem paths, blocked command patterns |
| `config/knowledge_sources.yaml`| Research document sources, scraping rules, ingestion targets |
| `config/mcp.yaml` | External Model Context Protocol server configurations and transport definitions |
| Root `.env` | Instance credentials, provider API keys, and environment overrides |

For document OCR capabilities on scanned PDFs and images, refer to [Local documents and OCR](docs/knowledge/local-ocr.md). Optional dependencies can be installed via `uv sync --frozen --extra ocr`.

---

## Quality Gates and Verification

The repository enforces eight distinct verification gates across code formatting, static typing, architectural boundaries, unit tests, integration tests, and evaluation fixtures.

### Backend Verification

```bash
# 1. Linting and code style
uv run ruff check .

# 2. Static type analysis
uv run mypy

# 3. Architectural layering contracts (enforces import rules across 14 layers)
uv run lint-imports --config importlinter.ini

# 4. Backend test suite
uv run pytest
```

### Frontend Verification

Run from the `frontend/` directory:

```bash
# 5. ESLint code analysis
npm run lint

# 6. Unit and component tests
npm run test:unit

# 7. Production bundle compilation
npm run build

# 8. End-to-end browser integration tests
npm run test:e2e
```

### Evaluation Benchmark Gate

A dedicated verification gate evaluates model answers against stored fixtures to verify regression safety without requiring live model quota:

```bash
uv run python scripts/eval_gate.py --answers eval/recorded/answers.json
```

Continuous integration runs all checks on pull requests and pushes to `main`. See [.github/workflows/ci.yml](.github/workflows/ci.yml) for full pipeline configuration.

---

## Operational Considerations and Security Boundaries

1. **Host Execution vs. Docker Sandboxing**: The development native executor runs commands directly on the host machine and does not establish an isolation boundary. For untrusted code or tasks, configure and enable the Docker tool sandbox.
2. **Filesystem Boundaries**: Path allowlists configured in `config/permissions.yaml` define intended directory scopes. For strict access segregation, run the service within an isolated container or dedicated user account.
3. **Audit Log Persistence**: SQLite audit logging provides forward-linked SHA-256 chain verification for chronological integrity. In high-concurrency environments, appends are serialized to prevent chain fork errors.
4. **Network Exposure**: By default, the API binds to loopback (`127.0.0.1`). If exposing the API beyond localhost, configure non-empty `ATLAS_API_KEYS` and terminate traffic behind a reverse proxy with TLS.
5. **Experimental Components**: Modules under `src/atlas/adaptation/` and `src/atlas/training/` provide experimental pipelines for trajectory evaluation and weight fine-tuning. See [IDE training documentation](docs/ide/training.md) for details on compute requirements and experimental constraints.

---

## Technical Documentation Index

- [Architecture Overview](ARCHITECTURE.md)
- [Runtime Startup Sequence](docs/runtime/STARTUP_SEQUENCE.md)
- [Runtime Readiness Model](docs/runtime/READINESS_MODEL.md)
- [Tool Catalog & Schema](docs/tooling/catalog.md)
- [Routing Architecture](docs/routing/architecture.md)
- [Execution Engine Architecture](docs/execution/architecture.md)
- [MCP Integration & Configuration](docs/tooling/mcp/configuration.md)

---

## License

This project is licensed under the [MIT License](LICENSE). Copyright 2026 Aman Bhaskar.
