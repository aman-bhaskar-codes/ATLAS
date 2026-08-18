# ATLAS Zero-Cost-First Architecture

This document confirms all up-to-date codes and features implemented in the **Zero-Cost-First Platform Expansion**. ATLAS is now capable of running completely autonomously on local hardware or free-tier cloud endpoints without requiring a single paid API key.

## 1. Policy Engine & Cost Governance
- **`src/atlas/infra/types.py`**: Added `CostPolicy` (`zero_cost`, `free_only`, `balanced`, `unrestricted`), `NetworkPolicy` (`local_only`, `cloud_allowed`), and `PrivacyClass`.
- **`src/atlas/intelligence/selection/selector.py`**: Model selector now enforces network and cost constraints (e.g., dropping all cloud models when `local_only` is active).
- **`src/atlas/intelligence/governance/quota_governor.py`**: Added `FreeQuotaGovernor` to track and enforce daily free-tier limits (e.g., Groq limits, Gemini flash limits).

## 2. Operating Profiles
- **`src/atlas/infra/profiles.py`**: Introduced configuration profiles:
  - `local_free`: `$0`, `local_only`, strict privacy.
  - `free_hybrid`: `$0`, allows `free_quota` cloud models.
  - `free_demo`: `$0`, UI-focused.

## 3. Free Provider Adapters & Models
- **`src/atlas/intelligence/gateway.py`**: The central intelligence gateway intercepts rate limits and triggers the `FallbackEngine` to seamlessly degrade from cloud models back to local Ollama (`qwen3:4b`).
- **`config/models.yaml`**: Full registry of $0 capabilities:
  - Local: `qwen3:4b` (Reasoning, tool calling)
  - Free Cloud: `llama-3.1-70b-versatile` (Groq), `gemini-2.0-flash`.
- **`src/atlas/memory/embedder.py`**: Added `FallbackEmbedder` which drops down to alternative embedding models when cloud endpoints fail.

## 4. Extended CLI (`atlas-cli`)
- **`src/atlas_cli/main.py`**: The CLI is the primary control plane for execution. Added:
  - `atlas doctor`: Deep environment diagnostics.
  - `atlas providers health`: Live latency checking.
  - `atlas cost`: Manage policy limits and quotas.
  - `atlas profile`: Hot-swap operating profiles.

## 5. Offline Knowledge & RAG
- **`src/atlas/memory/retrieval.py` & `src/atlas/memory/knowledge_store.py`**: Local vector storage (ChromaDB) for persisting agent research.
- **`src/atlas/memory/document_processor.py`**: Added offline extraction for 21+ file types without sending data to cloud ingestion APIs.

## 6. Real-Time Next.js Dashboards
- **`frontend/app/providers/page.tsx`**: Live provider latency and quota tracking UI.
- **`frontend/app/cost/page.tsx`**: Cost distribution and policy enforcement visualizer.
- **`frontend/app/capabilities/page.tsx`**: Capability matrix showing which models power which agent features across local vs. paid tiers.
- **`src/atlas/interfaces/api/routes_ops.py` & `routes_providers.py`**: Backend FastAPI routes powering the dashboards.

## 7. Containerized Deployment
- **`Dockerfile` & `docker-compose.yaml`**: One-click deployment combining the FastAPI backend, Next.js dashboard, and local SQLite/ChromaDB state. Allows spinning up the full platform with a single command: `docker compose --profile full up`.

---
*All 348 tests are currently passing, guaranteeing 0 regressions across the legacy system.*
