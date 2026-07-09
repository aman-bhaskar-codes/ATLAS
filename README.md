# ATLAS — Autonomous Task & Learning Agent System

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/mypy-strict-green?logo=python" />
  <img src="https://img.shields.io/badge/ruff-clean-orange?logo=ruff" />
  <img src="https://img.shields.io/badge/tests-78%20passing-brightgreen?logo=pytest" />
  <img src="https://img.shields.io/badge/uv-managed-purple" />
  <img src="https://img.shields.io/badge/license-MIT-lightgrey" />
</p>

> **ATLAS** is a production-grade autonomous AI agent framework built on Python 3.13, designed for local-first, safety-first, fully auditable agentic task execution. Every consequential action is classified, confirmed, and audited before execution. Nothing happens without your explicit consent.

---

## ✨ What ATLAS Is

ATLAS is a **multi-phase agentic runtime** — not a wrapper, not a chatbot, not a demo. It is a ground-up implementation of the full autonomous agent stack:

```
Perception → Memory → Planning → Reasoning → Dispatch → Safety → Execution
```

Built with a hard constraint: **the orchestrator never executes a tool directly**. Every tool call flows through an L1 Safety Engine (manifest + classifier + audit + killswitch) and requires human confirmation for Tier-2+ actions.

---

## 🏗️ Architecture

ATLAS is structured in independently testable, DI-wired layers:

```
┌──────────────────────────────────────────────────────────┐
│                  ATLAS Runtime Stack                     │
├────────────────────────┬─────────────────────────────────┤
│  L0 Infrastructure     │  bus, db, clock, ids, lifecycle  │
│  L1 Safety Engine      │  manifest, classifier, audit,    │
│                        │  killswitch, policy engine       │
│  L2 Intelligence       │  ModelGateway, CapabilityRouter, │
│   Platform             │  CostGovernor, HealthMonitor,    │
│                        │  FallbackEngine, CircuitBreaker  │
│  L3 Memory             │  Working, Episodic, Semantic,    │
│                        │  UserModel, HybridRetrieval,     │
│                        │  AutoConsolidation & Pruning     │
│  L4 Orchestration      │  ReasoningLoop (ReAct), Planner, │
│                        │  SelfCritique, Dispatcher,       │
│                        │  State Machine, Router           │
│  L5 Perception         │  macOS Accessibility Tree,       │
│                        │  Sensitivity classifier          │
│  L6 Control            │  Allowlisted AppleScript intents │
│  L7 Interfaces         │  CLI, ntfy notifier, confirmer   │
└────────────────────────┴─────────────────────────────────┘
```

### Key Design Principles

| Principle | Implementation |
|---|---|
| **Safety-first** | Deny-by-default. Every tool call is tier-classified before execution. Tier-2+ requires human confirmation |
| **Local-first** | Prefers Ollama (local) over cloud. Cloud only when explicitly enabled |
| **Fail-closed** | Budget unknown? Deny. Critique failure? Defer to Safety Engine. Revision failure? Ask human |
| **Fully auditable** | Every model call, tool dispatch, cost, and self-critique is written to an immutable audit log |
| **DI everywhere** | One composition root (`app.py`). No module self-constructs its dependencies |
| **Metadata is data** | Model capabilities are config, not code. Swap providers without touching logic |

---

## 🧠 Intelligence Platform (Phase 5A)

A production-grade LLM routing and governance layer:

- **Capability-based routing** — models are matched to tasks by declared capabilities (`REASONING`, `CODING`, `VISION`, `TOOL_CALLING`, etc.)
- **Multi-window cost governance** — daily / weekly / monthly / per-task USD budgets, fail-closed
- **Circuit breaker + health monitor** — per-provider rolling failure tracking; open breaker reroutes automatically
- **Ranked fallback chain** — `DeepSeek → GLM → Kimi → local Qwen → FallbackError`
- **Telemetry** — every inference call is audited with latency, cost, and model ID

---

## 🔁 In-Loop Self-Critique (Phase 4.5)

Before any Tier-2+ action is dispatched, ATLAS runs a **self-critique loop**:

```
ReasoningLoop produces Action
        │
        ▼
[Tier gate] Is action consequential? (Tier-2+ / risky)
   no  → pass through unchanged (no extra cost)
   yes → critique (local, thinking-off, tight budget):
           verdict ∈ {ok, revise, abort}
             ok     → proceed unchanged (Safety Engine still gates)
             revise → regenerate the action ONCE with the critique
             abort  → convert to ask_user(reason); never executed
        │
        ▼
[L1 Safety Engine] SafetyEngine.guard() — Tier-2 still confirms with human
```

**ADR-011 invariant**: self-critique can only make actions _safer_. A `ok` verdict does not lower the tier. `abort` stops the action regardless. The critique can never grant privilege.

---

## 🔌 External Capabilities Platform (Phase 6.1)

ATLAS makes it incredibly simple to manually integrate external tools via the **Capability Core**:

- **Unified Provider Protocol**: Implement a single `Provider` interface (`initialize`, `execute`, `normalize`, `health`) and it drops cleanly into the framework.
- **Provider Registry**: Handles automatic circuit breaking and fallback. If a provider fails, the `CapabilityRouter` transparently routes around it.
- **MCP Ready**: Natively structured to treat Model Context Protocol (MCP) servers identically to local script adapters.
- **Zero-Friction Tooling**: All external capabilities are dynamically wrapped as `Tool` instances that plug directly into the pre-existing safety and audit engine.
- **Ready for Manual Integrations**: Just create a provider class, bind it in `app.py`, and you're done!

---

## 🧬 Memory System (Phase 3)

Four-layer memory with hybrid retrieval:

| Layer | Storage | Purpose |
|---|---|---|
| **Working Memory** | In-process dict | Active scratchpad for current task |
| **Episodic Memory** | SQLite | Timestamped event log; auto-consolidates to semantic |
| **Semantic Memory** | ChromaDB + Ollama embeddings | Long-term knowledge; similarity search |
| **User Model** | SQLite | Learned preferences, constraints, interaction patterns |

**Auto-consolidation**: episodic memories are periodically distilled into semantic facts using the model gateway. **Pruning**: stale, low-value memories are garbage-collected to keep retrieval sharp.

---

## 🚀 Quickstart

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) — the package manager
- [Ollama](https://ollama.ai/) running locally (for local-first inference)
- Docker (optional, for sandboxed shell execution)

### Install & Run

```bash
git clone https://github.com/aman-bhaskar-codes/atlas
cd atlas

# Install all dependencies (creates .venv automatically)
uv sync --all-extras

# Run preflight checks
uv run atlas doctor

# Run the test suite
uv run pytest

# Start the agent
uv run atlas run "research the latest papers on multi-agent systems"
```

### Configuration

Copy `.env.example` to `.env` and configure your API keys:

```env
# Required for local inference (recommended)
OLLAMA_HOST=http://localhost:11434

# Optional cloud providers (only used if allow_cloud: true in settings.yaml)
DEEPSEEK_API_KEY=sk-...
GLM_API_KEY=...
KIMI_API_KEY=...

# Optional: ntfy push notifications for human confirmation
NTFY_TOPIC=my-atlas-confirms
```

Edit `config/models.yaml` to configure model capabilities and budget limits. Edit `config/settings.yaml` to tune execution behaviour.

---

## 📁 Project Layout

```
atlas/
├── src/atlas/
│   ├── infra/          # L0: Bus, DB, Clock, IDs, Config, Lifecycle
│   ├── safety/         # L1: Manifest, Classifier, Audit, KillSwitch, Policy
│   ├── intelligence/   # L2: Gateway, Registry, Router, Governor, Health, Fallback
│   │   ├── providers/      # Ollama, OpenAI-compatible, Anthropic, Gemini
│   │   ├── governance/     # CostGovernor, CircuitBreaker, RateLimiter, Budget
│   │   ├── selection/      # CapabilityRouter, ModelSelector
│   │   ├── runtime/        # InferenceRuntime, FallbackEngine, Streaming, Retry
│   │   └── observability/  # Telemetry
│   ├── memory/         # L3: Working, Episodic, Semantic, UserModel, Retrieval
│   ├── orchestration/  # L4: ReasoningLoop, Planner, Dispatcher, SelfCritique
│   │   └── managers/       # RetryManager
│   ├── perception/     # L5: macOS AX tree, Sensitivity classifier
│   ├── control/        # L6: AppleScript intents, tool abstraction
│   ├── tools/          # Filesystem, Shell (sandboxed via Docker)
│   ├── interfaces/     # CLI, ntfy notifier, human confirmer
│   └── app.py          # Single composition root — all DI wiring
├── tests/              # 78 tests, zero mocks for business logic
├── config/             # models.yaml, settings.yaml, permissions.yaml
└── pyproject.toml      # uv, ruff (line-length=120), mypy strict, pytest
```

---

## 🧪 Testing

```bash
# Full suite
uv run pytest

# By module
uv run pytest tests/intelligence/    # Intelligence platform
uv run pytest tests/orchestration/   # Reasoning loop + self-critique
uv run pytest tests/memory/          # Memory layers
uv run pytest tests/safety/          # Safety engine

# Type checking
uv run mypy src/ --strict

# Linting
uv run ruff check src/ tests/
```

**Current status**: 78 tests, 0 failures, `mypy --strict` clean on 113 source files.

---

## 🔒 Safety Model

ATLAS uses a **three-tier action classification**:

| Tier | Example | Behaviour |
|---|---|---|
| `AUTO` | Read a file, search the web | Executes immediately |
| `CONFIRM` | Write/delete a file, run a script | Requires human confirmation (ntfy + CLI fallback) |
| `BLOCK` | Actions outside the manifest | Hard-blocked, never executed |

The **manifest** (`config/permissions.yaml`) is the source of truth for what the agent is allowed to touch. Everything outside the manifest is `BLOCK` by default.

---

## 🗺️ Roadmap

| Phase | Status | Description |
|---|---|---|
| Phase 1 — Infrastructure | ✅ Done | L0 infra, L1 Safety Engine, tools, CLI |
| Phase 2 — Perception & Control | ✅ Done | macOS AX tree, AppleScript intents |
| Phase 3 — Memory | ✅ Done | 4-layer memory, hybrid retrieval, consolidation |
| Phase 4 — Orchestration Runtime | ✅ Done | ReAct loop, dispatcher, state machine, planner |
| Phase 4.5 — Self-Critique | ✅ Done | In-loop self-critique for Tier-2+ actions |
| Phase 5A — Intelligence Platform | ✅ Done | Multi-provider gateway, routing, governance |
| Phase 6.1 — Capability Core | ✅ Done | Pluggable external capability framework and provider registry |
| Phase 6.2 — Core Capabilities | 🔜 Next | Build out Identity, Knowledge, Browser, and MCP capabilities |
| Phase 7 — Advanced Agents | 🔜 Planned | Research agent, peer-review loops, multi-agent swarms |

---

## 📐 Design Decisions

**Why not LangChain/AutoGen/CrewAI?** ATLAS is intentionally hand-built. Framework abstractions obscure the safety model, make auditing impossible, and add uncontrollable dependencies. Every line of ATLAS does exactly what it says.

**Why SQLite for everything persistent?** Zero-dependency, zero-server, fully portable, and the audit log is just a file you can `grep`. The agent's memory travels with it.

**Why uv?** Fast, reproducible, lockfile-first. `uv sync` creates an exact environment in seconds.

**Why mypy --strict?** Type errors at the model boundary (wrong `frozenset` vs `list`) caused real bugs. Strict types catch them before runtime.

---

## 📜 License

MIT — see [LICENSE](LICENSE)

---

<p align="center">
  Built by <a href="https://github.com/aman-bhaskar-codes">@aman-bhaskar-codes</a> · Python 3.13 · uv · mypy --strict · ruff
</p>
