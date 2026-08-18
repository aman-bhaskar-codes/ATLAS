# Free Deployment Guide

Honest labels for deployment targets. No free tier here is suitable for
million-user inference; free is the smallest deployment profile.

| Target                    | Label                | Notes |
| ------------------------- | -------------------- | ----- |
| Local machine / laptop    | FREE                 | Default `local_free` profile; full functionality, Ollama + SQLite. |
| Docker Compose (local)    | FREE                 | `docker-compose.yaml` — ATLAS + Ollama + observability stack; add Postgres/Redis/pgvector via profiles later. |
| GitHub Pages / static     | FREE                 | Static frontend only — no backend. |
| Cloudflare Workers Free   | FREE WITH LIMITS     | Bounded daily request/CPU limits; demo/edge only, not a backend. |
| Hugging Face Spaces       | FREE WITH LIMITS     | Static Spaces for demo frontends; free compute-hosted Spaces are limited/eligibility-gated — do not assume persistent backend compute. |
| Supabase Free             | FREE WITH LIMITS     | Two free projects; optional managed Postgres/auth/storage. Local implementations remain fully functional; never required. |
| Fly.io / Render / VPS     | PAID AFTER QUOTA     | For the `production` profile when you outgrow local. |

## Recommended zero-cost demo architecture

```text
Frontend   → static hosting (GitHub Pages)
Backend    → local machine or bounded free edge profile
AI         → Ollama locally + optional free-tier APIs
Data       → SQLite locally (+ optional Supabase Free)
Cache      → in-process / SQLite
Observability → OTel + Prometheus locally
```

## Acceptance path

```bash
git clone https://github.com/aman-bhaskar-codes/ATLAS
cd ATLAS && uv sync
atlas setup && atlas doctor
atlas run "analyze this repository and explain the architecture"
```

No paid API, no mandatory cloud account, no mandatory external database.
