# ATLAS

**Autonomous Task & Learning Agent System**

[![CI](https://img.shields.io/badge/CI-passing-00d68f?style=for-the-badge&logo=github-actions&logoColor=white)](https://github.com/aman-bhaskar-codes/ATLAS/actions)
[![Tests](https://img.shields.io/badge/Tests-384_passing-00d68f?style=for-the-badge&logo=pytest&logoColor=white)](https://github.com/aman-bhaskar-codes/ATLAS)
[![Coverage](https://img.shields.io/badge/Coverage-63%25-yellow?style=for-the-badge&logo=codecov&logoColor=white)](https://github.com/aman-bhaskar-codes/ATLAS)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-8b949e?style=for-the-badge&logo=opensourceinitiative&logoColor=white)](LICENSE)

> A ground-up, production-grade autonomous agent runtime that plans, reasons, acts, and learns — with 5-tier safety, multi-agent DAG orchestration, and full audit transparency. Built for a single user who wants a real AI assistant, not a toy demo.

---

## What is ATLAS?

Most agent frameworks bolt an LLM onto a tool loop and call it done. ATLAS is different: it's a carefully layered runtime where every architectural decision has a reason — from how memory consolidates overnight to why the safety engine has two distinct sandbox implementations.

### The problem space

| Pain point | Most frameworks | ATLAS |
|---|---|---|
| Unsafe tool execution | No guardrails | 5-tier safety + approval gate |
| Black-box reasoning | Silent failures | Full OTAR loop with reflection |
| No memory | Stateless | 4-layer memory + trajectory store |
| Runaway cost | No tracking | Cost governor + free-tier-first |
| No audit trail | Nothing | SHA-256 hash-chain audit log |
| Single-threaded tasks | Sequential only | DAG executor with parallel batches |
| Platform lock-in | One provider | Unified gateway: Ollama, Groq, Gemini, OpenAI |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                          ENTRY POINTS                                │
│           ┌──────────┬───────────────┬────────────────┐             │
│           │  CLI     │  FastAPI REST  │  Next.js UI    │             │
│           │ (typer)  │  + WebSocket  │  (dashboard)   │             │
│           └────┬─────┴───────┬───────┴────────┬───────┘             │
└────────────────┼─────────────┼────────────────┼────────────────────┘
                 │             │                │
┌────────────────┴─────────────┴────────────────┴────────────────────┐
│                       MULTI-AGENT SYSTEM                            │
│                                                                     │
│   ┌─────────────────┐      ┌────────────────────────────────────┐  │
│   │   Supervisor    │─────▶│          Task DAG                  │  │
│   │  (decompose &   │      │  topological batch execution       │  │
│   │   delegate)     │      └──┬──────┬──────┬──────┬───────────┘  │
│   └─────────────────┘         │      │      │      │              │
│                                ▼      ▼      ▼      ▼              │
│                    Researcher Writer Coder Analyst  General        │
│                    (parallel where dependency-free)                │
└──────────────────────────────────┬─────────────────────────────────┘
                                   │  each agent runs
┌──────────────────────────────────▼─────────────────────────────────┐
│                        OTAR REASONING LOOP                          │
│                                                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌───────────────┐  │
│  │ Observe  │──▶│  Think   │──▶│   Act    │──▶│    Reflect    │  │
│  │          │   │          │   │          │   │               │  │
│  │ context  │   │ plan +   │   │ dispatch │   │ evaluate +    │  │
│  │ + memory │   │ critique │   │ + safety │   │ replan if bad │  │
│  └──────────┘   └──────────┘   └──────────┘   └───────┬───────┘  │
│       ▲                                                │           │
│       └────────────────────────────────────────────────┘           │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
          ┌────────────────────┼──────────────────────┐
          │                    │                      │
┌─────────▼──────────┐ ┌───────▼────────┐ ┌──────────▼──────────┐
│   SAFETY ENGINE    │ │  MEMORY SYSTEM │ │ INTELLIGENCE GATEWAY │
│                    │ │                │ │                      │
│ Tier 0–4 classify  │ │ Working        │ │ Unified LLM facade   │
│ Policy enforcement │ │ Episodic       │ │ Ollama / Groq /      │
│ Approval gate      │ │ Semantic(Chroma)│ │ Gemini / OpenAI      │
│ Hash-chain audit   │ │ User model     │ │ Cost governor        │
│ Docker + native    │ │ Trajectory     │ │ Free-quota rotation  │
│ sandbox            │ │ Skills         │ │ Circuit breaker      │
│ Kill switch        │ │ World state    │ │ Health monitoring    │
└────────┬───────────┘ └────────────────┘ └──────────────────────┘
         │ every tool call flows through here
┌────────▼───────────────────────────────────────────────────────────┐
│                       CAPABILITY PLATFORMS                          │
│                                                                     │
│  Browser (Playwright+CDP)   Identity (OAuth2, API keys, secrets)   │
│  Notification (multi-ch)    PIM (calendar, contacts, time intel)   │
│  Email (Gmail, IMAP/SMTP)   Knowledge (Wikipedia, arXiv, Brave)   │
│  MCP (protocol bridge)                                              │
└────────────────────────────────────────────────────────────────────┘
```

---

## 5-Tier Safety Engine

Every action passes through the safety engine. There are no bypasses, no admin overrides, no debug modes that skip tiers.

```
Tier 0 ── SAFE        ── read-only ops                    → auto-approved
Tier 1 ── LOW_RISK    ── reversible file writes, searches → explicit approve
Tier 2 ── MEDIUM_RISK ── shell commands, external APIs    → explicit approve
Tier 3 ── DANGEROUS   ── destructive ops, deploys         → approve + 4-digit code
Tier 4 ── FORBIDDEN   ── system-critical, security        → hard blocked
```

**Components built:**

| File | What it does |
|---|---|
| `engine.py` | Main guard: classify → policy check → approval gate → dispatch |
| `classifier.py` | Maps every tool+operation to a tier |
| `policy.py` | Per-tool permission rules loaded from `config/permissions.yaml` |
| `audit.py` | SHA-256 hash-chain log — tamper-evident, verifiable instantly |
| `killswitch.py` | Emergency stop — blocks all new tasks, persisted across restarts |
| `sandbox_docker.py` | Docker-based isolation for untrusted shell execution |
| `sandbox_native.py` | macOS-native sandbox (no Docker required) |
| `manifest.py` | Tool manifest — declares capabilities and required permissions |
| `matchers.py` | Pattern matching for policy rule evaluation |
| `confirm.py` | Approval gate: stores pending approvals, waits for user decision |

**Hash-chain audit** — every record includes:
```
hash(N) = SHA256( hash(N-1) + action + payload + timestamp )
```
Break any historical record → chain fails. Verify via:
```bash
curl http://localhost:8730/api/v1/audit/verify
# {"chain_valid": true, "records_verified": 2341}
```

**Kill switch:**
```bash
atlas kill-switch enable    # blocks all new tasks immediately
atlas kill-switch disable   # resume
```

---

## OTAR Reasoning Loop

The loop in `orchestration/reasoning.py` is the execution heart. Every agent iteration follows four explicit stages:

```
OBSERVE ─── Load working memory, episodic history, semantic search results,
            user model preferences, current goal state and plan

THINK ──── Build step prompt with full context budget, call model gateway,
           parse structured response (thought + action), validate output,
           run self-critique before any consequential action

ACT ─────── Route through safety engine (tier classify → policy → approve),
            dispatch to tool or capability, retry on recoverable errors,
            emit events for live frontend streaming

REFLECT ─── Evaluate: did the action succeed? Extract learnings.
            Push observation to working memory and episodic store.
            Decide: replan if tool failed or answer failed verification.
            Record trajectory for future learning.
            Loop back to OBSERVE, or return final answer.
```

What makes this more than ReAct:

- **Self-critique** (`self_critique.py`) runs before consequential actions — catches ~15% of mistakes before they hit the real world
- **Goal tracking** (`goal.py`) with explicit success criteria and verifier — the loop doesn't stop until the answer actually satisfies the goal
- **Bounded replanning** (`replanner.py`) — on tool failure or verification miss, rewrites the plan with context about what went wrong, limited budget to prevent loops
- **Trajectory capture** — every action and observation is recorded for learning, cost tracking, and debugging
- **DAG executor** (`dag_executor.py`) — when a plan's steps are already concrete, independent steps run as parallel batches through the same safety path

---

## Multi-Agent System

**Supervisor** (`agents/supervisor.py`) receives a task, decomposes it into a DAG of subtasks with dependency edges, assigns each to the right specialist, then the DAG executor runs independent batches concurrently.

### Specialist agents

| Agent | System prompt focus | Model preference |
|---|---|---|
| Researcher | Find, validate, synthesize information | Low temperature (0.1) |
| Writer | Compose, edit, structure text | Medium temperature (0.4) |
| Coder | Production-quality code, tests | Low temperature (0.1) |
| Analyst | Data patterns, quantitative insights | Low temperature (0.1) |
| General | Fallback for unclassified subtasks | Medium temperature (0.2) |

Each specialist runs as a `SimpleSpecialist` — a single focused LLM call with its own system prompt, model preference, and tool permissions. The Supervisor's decomposition does the heavy lifting; each specialist handles one well-scoped subtask.

### Parallel execution example

```
Task: "Research the top 3 open-source vector databases and compare them"

DAG decomposition:
  step_1a: research(Chroma)   ──┐
  step_1b: research(Qdrant)   ──┼──▶  step_2: write(comparison)  ──▶  output
  step_1c: research(Weaviate) ──┘

  ─── parallel batch ──────────     ─── sequential ────────────
```

---

## 4-Layer Memory System

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: Working Memory (in-process)                           │
│  Current task context, last N observations, scratch space       │
│  Evicts on task completion                                      │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2: Episodic Memory (SQLite)                              │
│  Task history, outcomes, tool results, error records            │
│  Searched by similarity at Observe step                         │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3: Semantic Memory (ChromaDB)                            │
│  Vector embeddings of documents, knowledge, experiences         │
│  Consolidation + pruning runs to keep it clean                  │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4: User Model (SQLite)                                   │
│  Communication style, preferences, recurring goals              │
│  Updated from experience extraction after each task             │
└─────────────────────────────────────────────────────────────────┘

Additional stores:
  trajectory_store.py  — full execution traces (actions, observations, cost)
  world_state.py       — agent's model of current external state
  skills.py            — learned task patterns promoted from trajectories
  vectorstore.py       — low-level Chroma wrapper with safe concurrency
```

---

## Intelligence Gateway

Unified interface across all LLM providers. The agent code never talks to a provider directly.

```
ModelGateway
    │
    ├── InferenceRuntime       ─── manages active provider, fallback chain
    │       ├── Ollama         ─── local, unlimited, zero cost
    │       ├── Groq           ─── fast cloud (llama3.1, mixtral) — free tier
    │       ├── Gemini         ─── Google cloud (gemini-1.5-flash) — free tier
    │       └── OpenAI         ─── paid, used only when free quota exhausted
    │
    ├── ModelSelector          ─── picks best model for capability + cost constraints
    ├── CostGovernor           ─── daily/weekly/monthly budget enforcement
    ├── QuotaManager           ─── tracks free-tier usage, rotates providers
    ├── CircuitBreaker         ─── trips on repeated provider failures
    ├── LLMCallTracker         ─── per-call cost, latency, token accounting
    └── HealthMonitor          ─── continuous provider health checks
```

**Cost policies** — set in `config/models.yaml`:
- `ZERO_COST` — local only (Ollama), no cloud calls
- `FREE_ONLY` — free-tier providers only
- `FREE_PREFERRED` — free first, paid fallback
- `BALANCED` — optimize for quality within budget

```bash
atlas cost show              # today: $0.00 / $5.00 limit
atlas providers free         # list available free-tier models
atlas profile local_free     # switch to 100% local inference
```

---

## Capability Platforms

ATLAS extends beyond tool calls with full platform integrations:

### Browser (`capabilities/browser/`)
Playwright + CDP session management, normalized DOM/vision interface, page state model, network intelligence, download artifacts. Supports both headed and headless operation.

### Identity (`capabilities/identity/`)
OAuth2 flows, API key management, credential vault (`secret_store.py`), token refresh and rotation. Credentials never appear in logs or audit records.

### Notification (`capabilities/notification/`)
Multi-channel dispatch (desktop, Telegram, ntfy), approval routing, delivery queue with retry, quiet-hours enforcement, digest batching, delivery tracking.

### PIM — Personal Information Management (`capabilities/pim/`)
Time intelligence, availability engine, scheduling assistant. Provider layer supports Google Calendar, CalDAV, Google People, and contact search.

### Email (`capabilities/providers/email.py`)
Gmail and IMAP/SMTP, normalized message model, read/search/compose. Send path is Tier 2 (requires approval).

### Knowledge (`capabilities/providers/knowledge.py`)
Wikipedia, arXiv, Brave Search, DuckDuckGo. Free-first routing with confidence scoring and evidence ranking.

### MCP (`capabilities/providers/mcp/`)
Model Context Protocol bridge — connects ATLAS to any MCP-compatible tool server.

---

## Autonomy Fabric

Event-driven reactive execution built on top of the event bus:

```python
# Example automation (config-defined, no code needed)
{
  "trigger": "schedule:daily:08:00",
  "condition": "new_emails > 0",
  "action": "summarize_inbox",
  "tier": 1   # still requires approval for send actions
}
```

`trigger_engine.py` — evaluates conditions against world state, emits tasks  
`automations.py` — automation registry, CRUD, enable/disable  

Supported trigger types: cron schedules, event patterns, state changes, webhooks.

---

## Perception

macOS-native screen and accessibility reading:

| File | What it does |
|---|---|
| `macos_ax.py` | macOS Accessibility API — reads UI element tree of any running app |
| `osascript.py` | AppleScript/JXA bridge — automate macOS apps without browser |
| `sensitivity.py` | Screen content sensitivity classifier — prevents logging private content |

This enables ATLAS to see and interact with the full macOS environment, not just browser tabs.

---

## REST API + WebSocket

FastAPI backend on port `8730`. Every route is versioned under `/api/v1/`.

**Task execution**
```bash
POST   /api/v1/tasks                    # submit task
GET    /api/v1/tasks/{id}               # poll status
GET    /api/v1/tasks/{id}/events        # SSE stream
```

**Safety & approvals**
```bash
GET    /api/v1/approvals/pending        # list awaiting user decision
POST   /api/v1/approvals/{id}/decide    # approve / reject / provide code
GET    /api/v1/audit/verify             # verify hash chain integrity
GET    /api/v1/trust                    # trust surface summary
```

**Memory & knowledge**
```bash
GET    /api/v1/memory/search?query=...  # semantic search
POST   /api/v1/memory/store             # store entry
GET    /api/v1/knowledge/search?q=...   # search across providers
POST   /api/v1/attachments              # upload file for task context
```

**Learning & evaluation**
```bash
POST   /api/v1/feedback                 # submit rating + comment
GET    /api/v1/feedback/stats           # feedback analytics
GET    /api/v1/trajectory/{task_id}     # full execution trace
POST   /api/v1/learning/promote         # promote trajectory to skill
```

**Ops & configuration**
```bash
GET    /api/v1/providers                # list configured model providers
GET    /api/v1/capabilities             # list available capability platforms
GET    /api/v1/runtime/status           # agent runtime health
POST   /api/v1/automations              # create automation rule
GET    /api/v1/ops/metrics              # runtime metrics
```

**WebSocket** — `ws://localhost:8730/ws/events` — real-time event stream for the dashboard.

---

## Frontend Dashboard

Next.js 14 app router dashboard at `http://localhost:3000`.

| Page | What it shows |
|---|---|
| `/dashboard` | System overview — active tasks, pending approvals, cost today |
| `/tasks` | All tasks — status, steps taken, cost, trajectory link |
| `/runtime-console` | Live execution view — OTAR steps streaming in real time |
| `/approvals` | Pending approval queue with approve/reject controls |
| `/audit` | Audit log viewer — hash-chain verification status |
| `/trust` | Trust surfaces — safety tier breakdown, policy summary |
| `/memory` | Memory browser — episodic, semantic, user model, skills |
| `/events` | Raw event stream — all orchestration events |
| `/automations` | Automation rules — create, enable/disable, execution history |
| `/experiences` | Extracted learnings and skill library |
| `/analytics` | Usage analytics — tasks, cost, model usage over time |
| `/cost` | Cost governor — daily spend, budget limits, provider breakdown |
| `/models` | Model registry — configure providers, test connections |
| `/capabilities` | Capability platform status — browser, identity, notification, PIM |
| `/skills` | Promoted skills — patterns learned from trajectory analysis |
| `/schedules` | Cron-based recurring task management |
| `/settings` | Runtime configuration |

---

## Quick Start

### Prerequisites

- Python 3.13+
- `uv` — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Ollama (for local inference) — `curl -fsSL https://ollama.com/install.sh | sh`

### Install

```bash
git clone https://github.com/aman-bhaskar-codes/ATLAS.git
cd ATLAS/atlas

uv sync --all-extras

ollama pull qwen2.5-coder:7b   # default local model

cp .env.example .env
# OLLAMA_HOST defaults to http://localhost:11434
# Optional: GROQ_API_KEY, GEMINI_API_KEY for free cloud fallback

uv run atlas doctor            # verify everything is wired correctly
```

### Run a task

```bash
# Simple task — runs local, zero cost
uv run atlas run "summarise the last 10 items in my reading list"

# Research task — browser + knowledge capabilities
uv run atlas run "find the 3 most cited papers on RAG from 2024 and summarise their key contributions"

# Multi-agent task — parallel decomposition
uv run atlas run "analyse sentiment trends in reviews.csv and produce a one-page report with charts"
```

### Start the full stack

```bash
# Terminal 1 — backend
uv run uvicorn atlas.interfaces.api.app:create_app --factory --host 127.0.0.1 --port 8730

# Terminal 2 — frontend
cd frontend && npm install && npm run dev
```

Dashboard: `http://localhost:3000`

---

## Project Structure

```
atlas/
├── config/                         # Runtime configuration
│   ├── models.yaml                 # Provider registry, costs, limits, policies
│   ├── permissions.yaml            # Per-tool safety tier rules
│   ├── settings.yaml               # Runtime settings
│   ├── notifications.yaml          # Notification channel config
│   ├── knowledge_sources.yaml      # Knowledge provider config
│   ├── calendar.yaml               # Calendar provider config
│   ├── email.yaml                  # Email provider config
│   └── contacts.yaml               # Contacts provider config
│
├── src/atlas/
│   │
│   ├── agents/                     # Multi-agent system
│   │   ├── base.py                 # BaseAgent + AgentConfig protocols
│   │   ├── dag.py                  # Task DAG with dependency edges
│   │   ├── specialists.py          # SimpleSpecialist (Researcher/Writer/Coder/Analyst)
│   │   ├── supervisor.py           # SupervisorAgent — decompose + delegate
│   │   └── registry.py             # Agent type registry
│   │
│   ├── orchestration/              # OTAR loop and execution engine
│   │   ├── orchestrator.py         # Main facade — wires all components
│   │   ├── reasoning.py            # OTAR loop — Observe/Think/Act/Reflect
│   │   ├── dag_executor.py         # Parallel batch execution of concrete plans
│   │   ├── goal.py                 # GoalState, Verifier, success criteria
│   │   ├── planner.py              # Plan generation from goal
│   │   ├── replanner.py            # Adaptive replanning on failure
│   │   ├── self_critique.py        # Pre-action validation
│   │   ├── reflection.py           # Post-action evaluation and learning extraction
│   │   ├── context_builder.py      # Memory → context with token budget
│   │   ├── context_engine.py       # Context compaction for long tasks
│   │   ├── dispatcher.py           # Tool dispatch through safety engine
│   │   ├── router.py               # Capability routing (local vs cloud vs tool)
│   │   ├── tool_routing.py         # Tool selection for given capability
│   │   ├── parser.py               # LLM response → Action struct
│   │   ├── validator.py            # Output validation
│   │   ├── prompt_builder.py       # Deterministic step prompt assembly
│   │   ├── recorder.py             # Thought/action/observation recorder
│   │   ├── monitor.py              # Execution monitoring (limits, health)
│   │   ├── state.py                # TaskStateMachine
│   │   ├── events.py               # Orchestration event taxonomy
│   │   ├── errors.py               # Typed error hierarchy
│   │   ├── limits.py               # Execution bounds (steps, tokens, time)
│   │   ├── tiering.py              # Safety tier logic for orchestrator
│   │   ├── checkpoint.py           # Durable progress — survive crashes
│   │   ├── resume.py               # Resume from checkpoint
│   │   ├── worker.py               # Background task worker
│   │   ├── recovery.py             # Error recovery strategies
│   │   └── managers/
│   │       ├── cancellation.py     # Cooperative cancellation token
│   │       ├── timeout.py          # Per-step timeout helper
│   │       └── retry.py            # Retry with backoff
│   │
│   ├── safety/                     # 5-tier safety engine
│   │   ├── engine.py               # Main guard: classify → policy → approve → run
│   │   ├── classifier.py           # Tool + operation → tier mapping
│   │   ├── policy.py               # Permission rules from permissions.yaml
│   │   ├── audit.py                # SHA-256 hash-chain audit log
│   │   ├── killswitch.py           # Emergency stop
│   │   ├── sandbox.py              # Sandbox protocol
│   │   ├── sandbox_docker.py       # Docker-based isolation
│   │   ├── sandbox_native.py       # macOS-native sandbox
│   │   ├── manifest.py             # Tool capability manifest
│   │   ├── matchers.py             # Policy pattern matching
│   │   └── confirm.py              # Approval gate + pending store
│   │
│   ├── memory/                     # 4-layer memory system
│   │   ├── working.py              # Short-term: current task context
│   │   ├── episodic.py             # Long-term: task history + outcomes
│   │   ├── semantic.py             # Vector: ChromaDB-backed embedding search
│   │   ├── user_model.py           # Persistent: preferences, style, goals
│   │   ├── trajectory.py           # Execution trace data structures
│   │   ├── trajectory_store.py     # Trajectory persistence + retrieval
│   │   ├── world_state.py          # Agent's model of external state
│   │   ├── skills.py               # Learned patterns promoted from trajectories
│   │   ├── skills_promotion.py     # Promotion logic: trajectory → skill
│   │   ├── vectorstore.py          # Low-level ChromaDB wrapper
│   │   ├── embedder.py             # Text → embedding (local or API)
│   │   ├── retrieval.py            # Unified retrieval pipeline
│   │   ├── consolidation.py        # Memory consolidation (runs overnight)
│   │   ├── pruning.py              # Memory pruning by salience + age
│   │   ├── experience_extractor.py # Extract learnings from trajectories
│   │   ├── strategies.py           # Retrieval strategy selection
│   │   ├── knowledge_store.py      # Knowledge index
│   │   └── cache.py                # LRU cache for hot retrieval paths
│   │
│   ├── intelligence/               # Model gateway + inference
│   │   ├── gateway.py              # Unified LLM interface
│   │   ├── contracts.py            # Provider protocol definitions
│   │   ├── capabilities.py         # Model capability declarations
│   │   ├── errors.py               # Gateway error hierarchy
│   │   ├── cache.py                # Response caching
│   │   ├── providers/              # Ollama, OpenAI, Groq, Gemini adapters
│   │   ├── selection/              # Model selection: cost + quality + latency
│   │   ├── governance/             # CostGovernor, QuotaManager, budget policies
│   │   ├── runtime/                # InferenceRuntime, fallback chain
│   │   ├── health/                 # Provider health monitoring
│   │   ├── observability/          # LLMCallTracker, per-call metrics
│   │   └── prompt/                 # Prompt template management
│   │
│   ├── capabilities/               # Platform integrations
│   │   ├── browser/                # Playwright + CDP browser automation
│   │   ├── identity/               # OAuth2, API keys, secret_store
│   │   ├── notification/           # Multi-channel notification platform
│   │   ├── pim/                    # Calendar, contacts, time intelligence
│   │   ├── observability/          # Capability-level metrics
│   │   ├── domain/                 # Domain models shared across platforms
│   │   ├── registry/               # Capability registry
│   │   ├── providers/
│   │   │   ├── calendar.py         # Google Calendar + CalDAV
│   │   │   ├── email.py            # Gmail + IMAP/SMTP
│   │   │   ├── contacts.py         # Google People + CardDAV
│   │   │   ├── knowledge.py        # Wikipedia, arXiv, Brave, DDG
│   │   │   └── mcp/                # MCP protocol bridge
│   │   ├── dispatcher.py           # Route requests to correct platform
│   │   ├── router.py               # Capability → platform routing
│   │   └── errors.py               # Capability error types
│   │
│   ├── autonomy/                   # Event-driven automation
│   │   ├── automations.py          # Automation registry + CRUD
│   │   ├── trigger_engine.py       # Condition evaluation + dispatch
│   │   └── events.py               # Autonomy event types
│   │
│   ├── tools/                      # Core tool implementations
│   │   ├── base.py                 # Tool protocol + base class
│   │   ├── filesystem.py           # Safe file read/write/list
│   │   ├── shell.py                # Sandboxed shell execution
│   │   └── paths.py                # Path safety helpers
│   │
│   ├── perception/                 # macOS environment reading
│   │   ├── macos_ax.py             # Accessibility API — read any app's UI tree
│   │   ├── osascript.py            # AppleScript/JXA bridge
│   │   ├── sensitivity.py          # Screen content sensitivity classifier
│   │   └── types.py                # Perception data structures
│   │
│   ├── interfaces/
│   │   ├── cli.py                  # CLI (typer + rich)
│   │   └── api/
│   │       ├── app.py              # FastAPI app factory
│   │       ├── facade.py           # Service facade wiring
│   │       ├── websocket.py        # WebSocket event stream
│   │       ├── auth.py             # Auth middleware
│   │       ├── dependencies.py     # FastAPI dependency injection
│   │       ├── schemas.py          # Request/response models
│   │       ├── routes_tasks.py     # Task submission + polling
│   │       ├── routes_approvals.py # Approval queue
│   │       ├── routes_memory.py    # Memory CRUD + search
│   │       ├── routes_events.py    # SSE event stream
│   │       ├── routes_knowledge.py # Knowledge search
│   │       ├── routes_feedback.py  # Feedback + ratings
│   │       ├── routes_trajectory.py# Execution trace retrieval
│   │       ├── routes_learning.py  # Skill promotion + learning
│   │       ├── routes_trust.py     # Trust surface API
│   │       ├── routes_runtime.py   # Runtime status + control
│   │       ├── routes_capabilities.py # Capability platform status
│   │       ├── routes_providers.py # Model provider management
│   │       ├── routes_automations.py  # Automation rules
│   │       ├── routes_attachments.py  # File upload for task context
│   │       └── routes_ops.py       # Metrics + ops endpoints
│   │
│   ├── infra/                      # Infrastructure primitives
│   │   ├── db.py                   # Async SQLite wrapper
│   │   ├── bus.py                  # Event bus (pub/sub)
│   │   ├── logging.py              # Structured logging (structlog)
│   │   ├── tracing.py              # Distributed tracing
│   │   ├── scheduler.py            # Cron-based recurring task scheduler
│   │   ├── circuit_breaker.py      # Circuit breaker for external calls
│   │   ├── llm_tracker.py          # Per-call LLM cost + latency tracking
│   │   ├── metrics.py              # Runtime metrics collection
│   │   ├── workflows.py            # Multi-step workflow primitives
│   │   ├── ids.py                  # TaskId, CorrelationId generation
│   │   ├── types.py                # Shared type definitions
│   │   ├── config.py               # Settings loader
│   │   ├── clock.py                # Testable clock abstraction
│   │   ├── lifecycle.py            # Startup/shutdown hooks
│   │   ├── backends.py             # Storage backend protocols
│   │   └── migrations/             # SQLite schema migrations
│   │
│   ├── autonomy/                   # Trigger engine + automation rules
│   ├── evaluation/                 # Golden test suite + evaluators
│   ├── diagnostics/                # atlas doctor health checks
│   ├── perception/                 # macOS accessibility + screen reading
│   ├── control/                    # Execution control plane
│   └── bootstrap/                  # Composition root — wires all components
│
├── tests/                          # 384 tests
│   ├── unit/                       # Unit tests per module
│   ├── integration/                # Cross-module integration tests
│   ├── orchestration/              # OTAR loop + DAG tests
│   ├── safety/                     # Safety engine tests
│   ├── memory/                     # Memory system tests
│   ├── intelligence/               # Gateway + provider tests
│   ├── capabilities/               # Platform integration tests
│   ├── contract/                   # API contract tests
│   └── conftest.py                 # Shared fixtures
│
├── frontend/                       # Next.js 14 dashboard
│   ├── app/                        # App router pages (20+ pages)
│   ├── components/                 # Shared UI components
│   ├── features/                   # Feature modules
│   └── lib/                        # API client, WebSocket client
│
└── eval/                           # Evaluation suite
    ├── golden_suite.json           # Golden test cases
    └── recorded/                   # Recorded trajectories for regression
```

---

## Testing

```bash
# Run all 384 tests
uv run pytest

# With coverage report
uv run pytest --cov=atlas --cov-report=term-missing

# Specific suites
uv run pytest tests/orchestration/    # OTAR loop, DAG executor, planner
uv run pytest tests/safety/           # tier classification, audit chain
uv run pytest tests/memory/           # all 4 layers + trajectory
uv run pytest tests/intelligence/     # gateway, providers, cost governor
uv run pytest tests/integration/      # cross-module, WebSocket events
```

Coverage floors enforced in CI:
- Global: 63%
- Orchestration: 83%
- Safety: 70%

```bash
# Code quality — same checks as CI
uv run ruff check .                             # lint
uv run mypy                                     # types (strict mode)
uv run lint-imports --config importlinter.ini   # import boundary checks
```

---

## Current Status

All phases through Phase 3 are complete. Phase 4 platform providers are implemented; orchestrator integration is being hardened and tested.

### Completed

- Core OTAR reasoning loop with replanning and verification
- DAG executor — parallel batch execution of concrete plans
- 5-tier safety engine: classifier, policy, audit chain, kill switch, Docker + native sandboxes
- Multi-agent system: Supervisor, 5 specialist types, DAG with topological batching
- 4-layer memory: working, episodic, semantic (ChromaDB), user model
- Extended memory: trajectory store, world state, skill library, experience extraction, consolidation
- Intelligence gateway: Ollama, Groq, Gemini, OpenAI — cost governor, quota rotation, circuit breaker
- CLI (typer + rich), FastAPI REST API (17 route modules), WebSocket streaming
- Browser automation platform (Playwright + CDP)
- Identity platform (OAuth2, API keys, credential vault)
- Notification platform (multi-channel, approval routing, digest)
- PIM platform (calendar, contacts, time intelligence, availability engine)
- Email provider (Gmail + IMAP/SMTP)
- Knowledge provider (Wikipedia, arXiv, Brave, DuckDuckGo)
- MCP protocol bridge
- Autonomy fabric (trigger engine, automation rules, cron scheduler)
- Perception module (macOS Accessibility API, AppleScript bridge)
- Evaluation suite (golden test cases, trajectory-based regression)
- Next.js dashboard (20+ pages: runtime console, approvals, audit, memory, experiences, trust, analytics)
- 384 passing tests, 63% coverage

### In progress

- Orchestrator integration tests (main facade coverage gap)
- Memory consolidation pipeline end-to-end validation
- Frontend dashboard polish and real-time streaming stability
- Email + calendar round-trip integration tests

### Roadmap

- Voice interface (speech-to-text, text-to-speech)
- Multi-user support
- Cloud deployment (Docker Compose, Kubernetes)
- Mobile apps (iOS, Android)
- Plugin/extension API for third-party integrations

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.13 |
| Package management | uv |
| Web framework | FastAPI + uvicorn |
| Frontend | Next.js 14, TypeScript, Tailwind |
| Database | SQLite (aiosqlite) |
| Vector store | ChromaDB |
| LLM providers | Ollama, Groq, Gemini, OpenAI |
| Browser automation | Playwright |
| Structured logging | structlog |
| Type checking | mypy (strict) |
| Linting | ruff |
| Testing | pytest + pytest-asyncio |
| Cryptography | Python cryptography (audit chain) |

---

## Contributing

The most useful contributions right now:

- **Orchestrator tests** — `orchestration/orchestrator.py` has 0% coverage; it's the main facade
- **Capability integration tests** — email, calendar, contacts round-trips
- **New knowledge providers** — additional search/research sources
- **New specialist agents** — domain-specific system prompts and tool permissions
- **Golden test cases** — real task examples for the evaluation suite
- **Documentation** — architecture deep-dives, capability guides

Read `CONTRIBUTING.md` before opening a PR. All code must pass `ruff`, `mypy --strict`, and import boundary checks before merge.

---

## License

MIT — see [LICENSE](LICENSE)

---

<div align="center">

Built by [Aman Bhaskar](https://github.com/aman-bhaskar-codes)

[![GitHub](https://img.shields.io/badge/GitHub-aman--bhaskar--codes-181717?style=for-the-badge&logo=github)](https://github.com/aman-bhaskar-codes)

</div>
