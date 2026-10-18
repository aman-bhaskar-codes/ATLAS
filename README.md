# ATLAS

**Autonomous Task & Learning Agent System**

ATLAS is a personal agent runtime project built with Python and a Next.js dashboard. It brings together task orchestration, model-provider routing, persistent memory, document retrieval, and development-workspace tools.

The repository explores how these components can share configuration, execution records, and permission checks across a command-line interface and an HTTP API. It also contains research-session workflows and experimental adaptation and local-training components.

## Main components

| Component | Technical scope |
| --- | --- |
| Task orchestration | Task state, planning, bounded reasoning loops, tool dispatch, cancellation, and execution records |
| Model access | A shared model gateway, provider adapters, selection rules, fallback handling, usage tracking, and configurable budgets |
| Memory | Working context, episodic records, semantic facts, user preferences, and stored task trajectories |
| Knowledge and research | Document ingestion, lexical and vector retrieval, rank fusion, source records, and persisted research sessions |
| Development workspaces | File trees, document editing, change previews, command execution, Git operations, diagnostics, and workspace sessions |
| Execution controls | Permission classification, approval requests, a stop flag, audit records, and Docker or development-mode host execution |
| Interfaces | Typer command-line interfaces, FastAPI routes, server-sent events, WebSockets, and a browser dashboard |

These entries describe the codebase's scope. Available workflows depend on configuration, provider access, installed dependencies, and the execution environment.

## Technology stack

| Area | Technologies |
| --- | --- |
| Backend | Python 3.13+, asyncio, FastAPI, Uvicorn |
| Configuration and contracts | Pydantic, Pydantic Settings, YAML |
| Persistence | SQLite with aiosqlite; PostgreSQL adapters with asyncpg for selected stores |
| Retrieval | ChromaDB, BM25, reciprocal-rank fusion, feature-based reranking |
| Model integrations | Ollama, Gemini, OpenRouter-compatible endpoints, and optional provider adapters |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS 4 |
| Client data and validation | TanStack Query, Zod |
| Tool integrations | Playwright and the Model Context Protocol SDK |
| Development tools | uv, pytest, Ruff, mypy, import-linter, Vitest, Playwright tests, GitHub Actions |
| Packaging | Hatchling, npm, Docker |

The Python and frontend lockfiles record dependency versions. Optional service definitions in `docker-compose.yml` cover SearXNG, Valkey, and Phoenix; starting those services does not start the ATLAS application.

## Code organization

| Path | Responsibility |
| --- | --- |
| `src/atlas/app.py` and `src/atlas/bootstrap/` | Component construction and runtime wiring |
| `src/atlas/orchestration/` | Tasks, reasoning, research runs, and execution coordination |
| `src/atlas/intelligence/` | Model gateway, providers, selection, budgets, and inference |
| `src/atlas/memory/` and `src/atlas/knowledge/` | Persistent context, ingestion, retrieval, and research evidence |
| `src/atlas/safety/` and `src/atlas/tools/` | Permission handling, approval queue, audit records, and tool implementations |
| `src/atlas/tooling/` | Tool registry, catalog, routing, execution graph, and MCP connections |
| `src/atlas/capabilities/` | Browser, workspace, voice, identity, and service-integration components |
| `src/atlas/infra/` | Database access, events, configuration, execution leases, and observability |
| `src/atlas/adaptation/`, `src/atlas/agents/`, and `src/atlas/training/` | Experimental strategy, agent, and retrieval-training components |
| `src/atlas/engineering/`, `src/atlas/perception/`, and `src/atlas/control/` | Engineering utilities and environment-specific perception/control adapters |
| `src/atlas/interfaces/` and `src/atlas_cli/` | API transports and command-line entry points |
| `frontend/` | Dashboard, research views, and development-workspace interface |
| `config/`, `tests/`, `eval/`, and `docs/` | Configuration, automated checks, evaluation fixtures, and technical notes |

The composition root builds shared services and passes them to the interfaces. The primary task path connects orchestration to tool dispatch and permission handling. Additional paths provide research sessions and workspace operations. Import-linter checks declared dependency boundaries.

## Local development

### Requirements

- Python 3.13 or later and `uv`.
- Node.js 22 and npm for the dashboard.
- Ollama with the configured chat and embedding models for local inference.
- Docker for container-based tool execution. Host execution is a separate development-mode option.
- Additional native dependencies for optional OCR, audio, and operating-system integrations.

### 1. Install the backend

```bash
git clone https://github.com/aman-bhaskar-codes/ATLAS.git
cd ATLAS
uv sync --frozen
```

Run backend commands from the repository root: configuration and `.env` loading use that working directory.

### 2. Configure a local instance

Create a root `.env` file. The following is a local starting configuration using model entries present in the repository:

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

Generate a private master key with the command below and place its output in `ATLAS_MASTER_KEY`. Keep it stable for an existing data directory.

```bash
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

With Ollama installed and running, download the models referenced above:

```bash
ollama pull qwen3:4b
ollama pull bge-m3:latest
```

Review `.env.example` for additional settings, replacing illustrative values rather than treating them as credentials. Model IDs, context limits, hardware requirements, and provider availability should be checked for the chosen configuration.

### 3. Start the API

```bash
uv run uvicorn atlas.interfaces.api.app:create_app --factory --host 127.0.0.1 --port 8730
```

The API documentation is served at `http://127.0.0.1:8730/api/docs`. In another terminal, the in-process diagnostics command is:

```bash
uv run python -m atlas.interfaces.cli doctor
```

The installed `atlas` command provides an HTTP client for task submission and inspection. Use `uv run atlas --help` to inspect its commands.

### 4. Start the dashboard

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3030`. The default API base is `http://localhost:8730/api/v1`; use `NEXT_PUBLIC_ATLAS_API_URL` in `frontend/.env.local` when changing it.

If backend authentication is enabled, enter the instance API key in the dashboard's Settings page. Keep provider keys and the master key on the backend; do not put them in `NEXT_PUBLIC_*` variables.

## Configuration and integrations

| File | Purpose |
| --- | --- |
| `config/settings.yaml` | Runtime options, inference preferences, feature switches, and execution limits |
| `config/models.yaml` | Model registry and provider metadata |
| `config/permissions.yaml` | Tool-operation rules, command categories, and filesystem paths |
| `config/knowledge_sources.yaml` | Research-source configuration |
| `config/mcp.yaml` | MCP server definitions and connection settings |
| Root `.env` | Instance settings and backend credentials |

SQLite is the default application database. Selected persistence stores can use PostgreSQL through domain-specific DSNs. ChromaDB stores vector indexes separately.

Cloud inference, web research, speech providers, and remote tools can send data to external services. Their availability, quotas, and charges depend on the service and account configuration. The `local_free` inference profile does not by itself disable every network-capable research or tool integration.

Browser automation requires a Playwright browser installation. Voice requires its feature flag, provider configuration, and optional audio dependencies. Desktop and device adapters depend on platform permissions and available software. MCP servers are owner-configured and disabled in the example configuration.

For PDFs and scanned documents, see [Local documents and OCR](docs/knowledge/local-ocr.md). The optional Python dependencies are installed with `uv sync --frozen --extra ocr`; scans additionally require Tesseract and Poppler.

## Development and evaluation

Backend checks:

```bash
uv run ruff check .
uv run mypy
uv run lint-imports --config importlinter.ini
uv run pytest
```

Frontend checks, run from `frontend/`:

```bash
npm run lint
npm run test:unit
npm run build
npm run test:e2e
```

Browser tests require their documented browser and backend prerequisites. The CI workflow is defined in [ci.yml](.github/workflows/ci.yml).

The recorded-answer gate scores stored fixtures; it does not run a fresh live-model benchmark:

```bash
uv run python scripts/eval_gate.py --answers eval/recorded/answers.json
```

Adaptation and training code includes trajectory processing, strategy evaluation, retrieval-weight tuning, and an optional local IDE training workflow. These are experimental components; dataset preparation, fixture scores, and stored reflections do not establish general self-improvement. See [IDE training](docs/ide/training.md) for compute prerequisites and evaluation boundaries.

## Runtime considerations

ATLAS is a development project for a trusted local environment. Review permitted paths, enabled integrations, and command rules before allowing it to act on a workspace.

The development-mode native executor runs on the host and is not an isolation boundary. Docker tool execution and the application Docker image serve different purposes. Keep an unauthenticated instance bound to loopback; configure authentication before exposing an instance beyond the local machine.

## Further documentation

- [Architecture overview](ARCHITECTURE.md)
- [Runtime startup](docs/runtime/STARTUP_SEQUENCE.md)
- [Runtime readiness](docs/runtime/READINESS_MODEL.md)
- [Tool catalog](docs/tooling/catalog.md)
- [Routing architecture](docs/routing/architecture.md)
- [Execution architecture](docs/execution/architecture.md)
- [MCP configuration](docs/tooling/mcp/configuration.md)

Some technical notes describe earlier development stages. Check the implementation and current configuration when following subsystem-specific instructions.

## License

[MIT](LICENSE). Copyright 2026 Aman Bhaskar.
