<!-- ╔═══════════════════════════════════════════════════════════════════════════╗ -->
<!-- ║                              ATLAS README                               ║ -->
<!-- ║            Autonomous Task & Learning Agent System                       ║ -->
<!-- ╚═══════════════════════════════════════════════════════════════════════════╝ -->

<div align="center">

<!-- ═══ ANIMATED CAPSULE HEADER ═══ -->
<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0d1117,50:1a1b27,100:0d1117&height=220&section=header&text=A%20T%20L%20A%20S&fontSize=80&fontColor=58a6ff&animation=fadeIn&fontAlignY=35&desc=Autonomous%20Task%20%26%20Learning%20Agent%20System&descAlignY=55&descSize=18&descColor=8b949e" width="100%" />

<!-- ═══ ANIMATED TYPING ═══ -->
<a href="https://github.com/aman-bhaskar-codes/ATLAS">
  <img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=600&size=22&duration=3000&pause=1000&color=58A6FF&center=true&vCenter=true&multiline=true&repeat=true&width=700&height=80&lines=Production-grade+autonomous+AI+agent+framework;5-tier+safety+%E2%80%A2+Multi-agent+DAGs+%E2%80%A2+OTAR+reasoning;Nothing+runs+without+your+permission.+Ever." alt="Typing SVG" />
</a>

<br/>

<!-- ═══ ANIMATED BADGES ═══ -->
<p>
  <a href="https://github.com/aman-bhaskar-codes/ATLAS/actions"><img src="https://img.shields.io/badge/CI-passing-00d68f?style=for-the-badge&logo=github-actions&logoColor=white&labelColor=0d1117" alt="CI" /></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.13-3776AB?style=for-the-badge&logo=python&logoColor=white&labelColor=0d1117" alt="Python" /></a>
  <a href="https://docs.astral.sh/uv/"><img src="https://img.shields.io/badge/uv-managed-7c3aed?style=for-the-badge&logo=astral&logoColor=white&labelColor=0d1117" alt="uv" /></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/badge/Ruff-linted-ef5552?style=for-the-badge&logo=ruff&logoColor=white&labelColor=0d1117" alt="Ruff" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-f0883e?style=for-the-badge&logo=opensourceinitiative&logoColor=white&labelColor=0d1117" alt="License" /></a>
</p>

<p>
  <a href="#-quickstart"><img src="https://img.shields.io/badge/🚀_Quick_Start-0d1117?style=for-the-badge" /></a>
  <a href="#-architecture"><img src="https://img.shields.io/badge/🏗️_Architecture-0d1117?style=for-the-badge" /></a>
  <a href="#-safety-engine"><img src="https://img.shields.io/badge/🛡️_Safety-0d1117?style=for-the-badge" /></a>
  <a href="#-multi-agent-system"><img src="https://img.shields.io/badge/🤖_Agents-0d1117?style=for-the-badge" /></a>
  <a href="#-api-reference"><img src="https://img.shields.io/badge/📡_API-0d1117?style=for-the-badge" /></a>
</p>

<!-- ═══ HERO BANNER ═══ -->
<img src="assets/atlas-banner.png" alt="ATLAS Neural Banner" width="100%" style="border-radius: 12px;" />

<br/><br/>

</div>

<!-- ═══ GRADIENT DIVIDER ═══ -->
<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 🌌 What is ATLAS?

> **ATLAS is not another LLM wrapper.** It is a ground-up, multi-phase autonomous agent runtime that plans, reasons, acts, and learns — while never doing anything without your explicit permission.

<table>
<tr>
<td width="50%">

### The Problem
Most AI agent frameworks are either:
- 🚫 **Unsafe** — run tools with no guardrails
- 🧸 **Toy-grade** — can't handle real multi-step tasks
- 🔒 **Black boxes** — no audit trail, no transparency

### The ATLAS Solution
A production-grade system with:
- ✅ **5-tier safety** with confirmation codes
- ✅ **Tamper-proof audit chain** (SHA-256)
- ✅ **Multi-agent DAG** decomposition
- ✅ **OTAR reasoning** (Observe → Think → Act → Reflect)
- ✅ **140+ tests**, zero mocked business logic

</td>
<td width="50%">

```text
╔══════════════════════════════════════╗
║         ATLAS Runtime Flow           ║
╠══════════════════════════════════════╣
║                                      ║
║   📥 Task Intake                     ║
║    ↓                                 ║
║   🧠 Memory Retrieval               ║
║    ↓                                 ║
║   📋 Plan Generation                ║
║    ↓                                 ║
║   🔄 OTAR Loop ──────────────┐      ║
║    │  Observe                 │      ║
║    │  Think                   │      ║
║    │  Act → 🛡️ Safety Gate   │      ║
║    │  Reflect                 │      ║
║    └──────────────────────────┘      ║
║    ↓                                 ║
║   📊 Evaluation & Learning          ║
║                                      ║
╚══════════════════════════════════════╝
```

</td>
</tr>
</table>

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 🏗️ Architecture

<div align="center">

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1a1b27', 'primaryTextColor': '#c9d1d9', 'primaryBorderColor': '#58a6ff', 'lineColor': '#58a6ff', 'secondaryColor': '#161b22', 'tertiaryColor': '#0d1117', 'edgeLabelBackground': '#0d1117'}}}%%

graph TB
    subgraph ENTRY["🌐 Entry Points"]
        CLI["⌨️ CLI"]
        API["📡 FastAPI"]
        CRON["⏰ Scheduler"]
    end

    subgraph AGENTS["🤖 Multi-Agent System"]
        SUP["👑 Supervisor"]
        DAG["📊 Task DAG"]
        R["🔬 Researcher"]
        W["✍️ Writer"]
        C["💻 Coder"]
        A["📈 Analyst"]
    end

    subgraph OTAR["🔄 OTAR Reasoning Loop"]
        O["👁️ Observe"]
        T["🧠 Think"]
        ACT["⚡ Act"]
        REF["🪞 Reflect"]
    end

    subgraph INTEL["🧪 Intelligence Platform"]
        GW["🔌 Model Gateway"]
        ROUTE["🎯 Model Router"]
        COST["💰 Cost Governor"]
    end

    subgraph SAFETY["🛡️ 5-Tier Safety Engine"]
        CLASS["📋 Tier Classifier"]
        POLICY["📜 Policy Chain"]
        AUDIT["🔗 Hash-Chain Audit"]
        KS["🔴 Kill Switch"]
    end

    subgraph MEMORY["🧠 4-Layer Memory"]
        WM["📝 Working"]
        EM["📚 Episodic"]
        SM["🧲 Semantic"]
        UM["👤 User Model"]
    end

    subgraph EXEC["⚙️ Execution"]
        TOOLS["🔧 Tool Registry"]
        SAND["🐳 Docker Sandbox"]
    end

    subgraph LEARN["📊 Learning Loop"]
        FB["👍 Feedback"]
        WF["🔄 Workflows"]
        LLM["💰 LLM Tracker"]
    end

    ENTRY --> SUP
    SUP --> DAG
    DAG --> R & W & C & A
    R & W & C & A --> OTAR
    O --> T --> ACT --> REF --> O
    ACT --> SAFETY
    SAFETY --> EXEC
    OTAR <--> INTEL
    OTAR <--> MEMORY
    EXEC --> LEARN
```

</div>

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 🛡️ Safety Engine

<div align="center">

> *"Nothing executes without flowing through the safety engine. Period."*

</div>

ATLAS uses a **5-tier, deny-by-default** classification system. Every tool call is classified before execution, and the entire audit log is protected by a **SHA-256 hash chain** — making it cryptographically tamper-proof.

<table>
<tr>
<th>Tier</th>
<th>Name</th>
<th>Risk</th>
<th>Behavior</th>
<th>Example</th>
</tr>
<tr>
<td><code>0</code></td>
<td><strong>🟢 AUTO</strong></td>
<td>None</td>
<td>Auto-approve, log silently</td>
<td>Read a file, search memory</td>
</tr>
<tr>
<td><code>1</code></td>
<td><strong>🔵 NOTIFY</strong></td>
<td>Low</td>
<td>Auto-approve with notification</td>
<td>Navigate browser, write to allowed path</td>
</tr>
<tr>
<td><code>2</code></td>
<td><strong>🟡 CONFIRM</strong></td>
<td>Medium</td>
<td>Require explicit user approval</td>
<td>Send email, delete file, run shell command</td>
</tr>
<tr>
<td><code>3</code></td>
<td><strong>🟠 DANGEROUS</strong></td>
<td>High</td>
<td>Approval + 4-digit confirmation code</td>
<td>Drop database, modify config, spend money</td>
</tr>
<tr>
<td><code>4</code></td>
<td><strong>🔴 BLOCK</strong></td>
<td>Critical</td>
<td>Never executed</td>
<td>Access credentials, financial transactions</td>
</tr>
</table>

<details>
<summary><strong>🔗 Hash-Chain Audit (click to expand)</strong></summary>
<br/>

Every audit record includes:
- `prev_hash` — SHA-256 of the previous record
- `row_hash` — SHA-256(prev_hash + action + payload + timestamp)

If **any** historical record is modified, the chain breaks. Verify integrity with:

```bash
# API endpoint
curl http://localhost:8730/api/v1/audit/verify

# Response
{
  "chain_valid": true,
  "records_verified": 1847,
  "status": "✓ Audit chain intact"
}
```

</details>

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 🤖 Multi-Agent System

Complex tasks are automatically decomposed by the **Supervisor Agent** into a **directed acyclic graph (DAG)** of subtasks. Independent subtasks execute in **parallel**, while dependent ones wait.

<div align="center">

```mermaid
%%{init: {'theme': 'dark'}}%%
graph LR
    TASK["📋 Complex Task"] --> SUP["👑 Supervisor"]
    SUP --> |decompose| DAG["📊 DAG"]

    DAG --> R1["🔬 Research\n(parallel)"]
    DAG --> R2["🔬 Research\n(parallel)"]
    R1 --> W["✍️ Writer\n(waits)"]
    R2 --> W
    W --> C["💻 Coder\n(waits)"]
    C --> SYNTH["🧬 Synthesize"]
```

</div>

<table>
<tr>
<th>Agent</th>
<th>Role</th>
<th>Allowed Tools</th>
</tr>
<tr><td>🔬 <strong>Researcher</strong></td><td>Find, analyze, synthesize information</td><td><code>browser</code> <code>knowledge</code> <code>memory</code> <code>http</code></td></tr>
<tr><td>✍️ <strong>Writer</strong></td><td>Compose, edit, refine text</td><td><code>filesystem</code> <code>knowledge</code> <code>memory</code></td></tr>
<tr><td>💻 <strong>Coder</strong></td><td>Write production-quality code</td><td><code>filesystem</code> <code>shell</code> <code>code</code> <code>knowledge</code></td></tr>
<tr><td>📈 <strong>Analyst</strong></td><td>Data analysis, pattern recognition</td><td><code>filesystem</code> <code>shell</code> <code>code</code> <code>knowledge</code></td></tr>
<tr><td>🌐 <strong>General</strong></td><td>Fallback for unclassified tasks</td><td><code>filesystem</code> <code>shell</code> <code>browser</code> <code>knowledge</code> <code>memory</code></td></tr>
</table>

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 🔄 OTAR Reasoning Loop

Every agent iteration follows the **Observe → Think → Act → Reflect** cycle:

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌─────┐ │
│   │ OBSERVE  │───▶│  THINK   │───▶│   ACT    │───▶│  R  │ │
│   │          │    │          │    │          │    │  E  │ │
│   │ Retrieve │    │ Reason   │    │ Execute  │    │  F  │ │
│   │ context  │    │ over     │    │ through  │    │  L  │ │
│   │ + memory │    │ options  │    │ safety   │    │  E  │ │
│   │          │    │          │    │ engine   │    │  C  │ │
│   └──────────┘    └──────────┘    └──────────┘    │  T  │ │
│        ▲                                          │     │ │
│        └──────────────────────────────────────────┘     │ │
│                     loop until done                      │ │
└─────────────────────────────────────────────────────────────┘
```

<details>
<summary><strong>🪞 What does Reflect do? (click to expand)</strong></summary>
<br/>

After every action, the agent evaluates:
- **Did the action succeed?** → track in `ReflectionResult`
- **What did we learn?** → extract error messages, side effects
- **Should we adjust the plan?** → flag `should_adjust_plan` if the action failed

This prevents the agent from repeating the same mistakes.

</details>

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 🧠 Memory System

<table>
<tr>
<td align="center" width="25%">
<h3>📝</h3>
<strong>Working Memory</strong>
<br/><br/>
Short-term scratchpad. Cleared per-task. Holds current context, tool results, and intermediate thoughts.
</td>
<td align="center" width="25%">
<h3>📚</h3>
<strong>Episodic Memory</strong>
<br/><br/>
Event log. Stores what happened, when, and how it turned out. Enables pattern recognition across tasks.
</td>
<td align="center" width="25%">
<h3>🧲</h3>
<strong>Semantic Memory</strong>
<br/><br/>
Vector embeddings via ChromaDB. Retrieves knowledge by meaning, not keywords. Auto-consolidates similar entries.
</td>
<td align="center" width="25%">
<h3>👤</h3>
<strong>User Model</strong>
<br/><br/>
Learns your preferences, communication style, and frequently used patterns to personalize responses.
</td>
</tr>
</table>

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 🚀 Quickstart

### Prerequisites

<table>
<tr>
<td><img src="https://img.shields.io/badge/Python-3.13+-3776AB?style=flat-square&logo=python&logoColor=white" /></td>
<td><img src="https://img.shields.io/badge/uv-latest-7c3aed?style=flat-square&logo=astral&logoColor=white" /></td>
<td><img src="https://img.shields.io/badge/Ollama-running-000000?style=flat-square&logo=ollama&logoColor=white" /></td>
</tr>
</table>

### 1️⃣ Install

```bash
git clone https://github.com/aman-bhaskar-codes/ATLAS.git
cd ATLAS/atlas
uv sync --all-extras
```

### 2️⃣ Configure

```bash
cp .env.example .env
# Edit .env — set OLLAMA_HOST, add any cloud API keys
```

### 3️⃣ Verify

```bash
uv run atlas doctor        # preflight health check
```

### 4️⃣ Run

```bash
# Dispatch a task to your autonomous agent
uv run atlas run "research the latest papers on multi-agent systems and save the summary"
```

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 💻 Web Dashboard

ATLAS includes a real-time Next.js dashboard for monitoring agent reasoning, approvals, and memories.

```bash
# Terminal 1: Backend
uv run uvicorn atlas.interfaces.api.app:create_app --factory --host 127.0.0.1 --port 8730 --reload

# Terminal 2: Frontend
cd frontend && npm install && npm run dev
```

Open **[http://localhost:3000](http://localhost:3000)** → live agent tracking dashboard.

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 📡 API Reference

<details>
<summary><strong>Tasks & Execution</strong></summary>
<br/>

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/tasks` | Submit a new task |
| `POST` | `/api/v1/tasks/{id}/cancel` | Cancel a running task |
| `GET` | `/api/v1/tasks/{id}/events` | SSE event stream for task |

</details>

<details>
<summary><strong>Safety & Approvals</strong></summary>
<br/>

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/approvals/pending` | List pending approvals |
| `POST` | `/api/v1/approvals/{id}/decide` | Approve/deny an action |
| `GET` | `/api/v1/audit/verify` | Verify hash chain integrity |

</details>

<details>
<summary><strong>Feedback & Evaluation</strong></summary>
<br/>

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/feedback` | Submit thumbs up/down + optional edit |
| `GET` | `/api/v1/feedback/stats` | Aggregate feedback statistics |
| `GET` | `/api/v1/schedules` | List recurring schedules |
| `POST` | `/api/v1/schedules` | Create a cron schedule |

</details>

<details>
<summary><strong>Memory & Knowledge</strong></summary>
<br/>

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/memory/search` | Semantic memory search |
| `POST` | `/api/v1/memory/store` | Store a memory entry |
| `GET` | `/api/v1/capabilities` | List registered capabilities |

</details>

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 📁 Project Structure

<details>
<summary><strong>Click to expand full project tree</strong></summary>

```
atlas/
├── config/                     # Configuration files
│   ├── models.yaml             # Model registry (providers, costs)
│   ├── permissions.yaml        # 5-tier safety rules
│   └── settings.yaml           # Runtime settings
├── src/atlas/
│   ├── agents/                 # 🤖 Multi-Agent System
│   │   ├── base.py             # BaseAgent + AgentConfig
│   │   ├── dag.py              # TaskDAG + topological sort
│   │   ├── registry.py         # Agent registry
│   │   ├── specialists.py      # 5 specialist agents
│   │   └── supervisor.py       # Supervisor decomposition
│   ├── capabilities/           # 🔧 Tool implementations
│   │   ├── browser/            # Browser automation
│   │   ├── identity/           # Identity & auth
│   │   ├── notification/       # Push/email notifications
│   │   └── platforms/          # Email, calendar, contacts
│   ├── infra/                  # 🏗️ Infrastructure
│   │   ├── db.py               # SQLite + migrations
│   │   ├── feedback.py         # Feedback store
│   │   ├── llm_tracker.py      # LLM cost tracking
│   │   ├── scheduler.py        # Cron scheduler
│   │   ├── types.py            # Shared contracts (5-tier)
│   │   └── workflows.py        # Workflow templates
│   ├── intelligence/           # 🧪 Model gateway & routing
│   │   ├── gateway.py          # Multi-provider LLM gateway
│   │   ├── governance/         # Cost governor, budgets
│   │   └── selection/          # Model selector, router
│   ├── interfaces/             # 🌐 API & CLI
│   │   └── api/                # FastAPI routes
│   ├── memory/                 # 🧠 4-layer memory
│   │   ├── episodic.py         # Event log
│   │   ├── semantic.py         # Vector embeddings
│   │   ├── working.py          # Task scratchpad
│   │   └── user_model.py       # User preferences
│   ├── orchestration/          # 🔄 OTAR runtime
│   │   ├── reasoning.py        # OTAR reasoning loop
│   │   ├── reflection.py       # Reflect step
│   │   ├── self_critique.py    # Pre-action critique
│   │   └── planner.py          # Plan generation
│   ├── safety/                 # 🛡️ Safety engine
│   │   ├── audit.py            # Hash-chain audit log
│   │   ├── classifier.py       # 5-tier classifier
│   │   ├── engine.py           # Safety engine (reference monitor)
│   │   └── policy.py           # Policy chain
│   └── app.py                  # 🎯 Composition root
├── tests/                      # 140+ tests
├── frontend/                   # Next.js dashboard
└── pyproject.toml              # Project config
```

</details>

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 🧪 Testing

```bash
# Full test suite (140+ tests)
uv run pytest

# With verbose output
uv run pytest -v

# Linter and type checking
uv run ruff check .
uv run mypy
```

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 🗺️ Roadmap

<table>
<tr>
<td>✅</td>
<td><strong>Phase 1–5</strong></td>
<td>Safety Engine, Orchestration, Memory, Intelligence Routing, Identity Platform</td>
</tr>
<tr>
<td>✅</td>
<td><strong>Phase 6</strong></td>
<td>5-Tier Safety + SHA-256 Hash-Chain Audit, OTAR Reasoning Loop</td>
</tr>
<tr>
<td>✅</td>
<td><strong>Phase 7</strong></td>
<td>Multi-Agent DAG System, Evaluation Loop, Feedback Store, LLM Tracker, Workflow Templates</td>
</tr>
<tr>
<td>🔜</td>
<td><strong>Phase 8</strong></td>
<td>RAG Pipeline Integration, Earned Autonomy Levels</td>
</tr>
<tr>
<td>🔮</td>
<td><strong>Phase 9</strong></td>
<td>Peer-Review Agent Swarms, Cross-Agent Learning</td>
</tr>
<tr>
<td>🔮</td>
<td><strong>Phase 10</strong></td>
<td>Voice Interface, Mobile Companion, Plugin Marketplace</td>
</tr>
</table>

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 🤝 Contributing

Contributions are welcome! Please read the contributing guidelines before submitting a PR.

1. Fork the repo
2. Create your feature branch (`git checkout -b feat/amazing-feature`)
3. Run tests (`uv run pytest`)
4. Commit your changes (`git commit -m 'feat: add amazing feature'`)
5. Push to the branch (`git push origin feat/amazing-feature`)
6. Open a Pull Request

<img src="https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/aqua.png" alt="divider" />

## 📜 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

<br/>

<!-- ═══ ANIMATED FOOTER ═══ -->
<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0d1117,50:1a1b27,100:0d1117&height=120&section=footer" width="100%" />

<br/>

<img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=500&size=14&duration=4000&pause=2000&color=8b949e&center=true&vCenter=true&repeat=true&width=400&height=30&lines=Built+with+%E2%9D%A4%EF%B8%8F+by+Aman+Bhaskar" alt="Footer" />

<br/>

<a href="https://github.com/aman-bhaskar-codes"><img src="https://img.shields.io/badge/GitHub-aman--bhaskar--codes-181717?style=flat-square&logo=github&logoColor=white" /></a>

</div>
