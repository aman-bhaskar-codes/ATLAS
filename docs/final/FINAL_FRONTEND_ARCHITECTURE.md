# FINAL_FRONTEND_ARCHITECTURE — ATLAS (verified)

## Stack

Next.js **16.2.11** + React **19** (App Router, `"use client"` islands), TypeScript,
Tailwind CSS **v4** (`@import "tailwindcss"` + `@theme` token mapping in
`app/globals.css`), `@tanstack/react-query` v5, `zod` v4, `lucide-react`. Constraint:
`frontend/AGENTS.md` warns Next 16 diverges from training data — consult
`node_modules/next/dist/docs/` before writing.

## Shell

`app/layout.tsx` → `<html className="dark">` → `Providers` (react-query) → `.app` grid
(`Sidebar` 240px + `.main` containing `Topbar` + page). `MobileNav` for ≤720px.

- `components/layout/Sidebar.tsx` — grouped nav (COMMAND/RUNTIME/AUTONOMY/TRUST/
  INTELLIGENCE/LEARNING/SYSTEM). Every link points to an existing route today (no target-IA
  sections like Research/Workspaces/Knowledge/Lab/System).
- `components/layout/Topbar.tsx` — breadcrumb + actions. **Contains fakes** (see debt):
  hardcoded "Telegram bridge connected", `alert()`-only health, non-functional kill switch.

## Data layer

- `lib/api/client.ts` — `API_BASE = env.NEXT_PUBLIC_ATLAS_API_URL ?? http://localhost:8730/api/v1`.
  Two styles: **typed** `request(path, zodSchema)` (validated, used by `atlasApi`) and
  **untyped** `requestJSON(path)` (returns `unknown`/`as` casts — `trustApi`, `autonomyApi`,
  `learningApi`, `opsApi`, `providersApi`). 8s timeout via `AbortController`.
- `lib/api/contracts.ts` — zod schemas mirroring backend Pydantic (`RuntimeStatus`,
  `RuntimeHealth`, `Task`, `TaskEvent`, `Approval`, `Capability`, …) + domain selectors
  (`isTerminal`, `canCancel`, `elapsedSeconds`).
- `features/runtime-console/*` — production-grade SSE hooks (`useTaskEvents`,
  `useTaskRuntime`, `useCancelTask`, `reconcile`). `features/{memory,trust,autonomy}/*` —
  typed query/mutation layers.
- `lib/websocket/*`, `lib/events/socket.ts` — WS/event client.

## Components

- `components/primitives/` — design system: `Button`, `IconButton`, `Badge`, `Panel`,
  `EmptyState`, `ErrorState`, `Skeleton`.
- `components/runtime/` — `LiveRunPage`, `ActivityTimeline`, `TaskStateBadge`,
  `EventConnectionBadge`, `RuntimeHealthPanel` (all real, SSE-driven).
- `components/trust/` — `ApprovalCard`, `DecisionBadge`, `ExactPreview` (real approval flow).
- `components/dashboard/` — Home widgets: `HeroSection`, `ActivityTimeline`,
  `ApprovalInbox`, `CapabilityPosture` (mix of real + fabricated data — see debt).
- `components/workspace/` — `CommandWorkspace` (real `createTask` → `/tasks/{id}`) +
  composer sub-parts (editor, attachments, context, preflight).
- `components/command/` — `CommandComposer`, `IntentReceipt` (task composer; **not** a ⌘K palette).

## Styling

`styles/tokens.css` (oklch palette: ink/paper/royal/gold/jade/ember/danger + spacing/
radius/ease) → mapped to Tailwind `--color-*` via `@theme`. `styles/atlas.css` holds the
hand-authored class system (`.rail`, `.hero`, `.health`, `.command`, `.topbar`,
`.nav a.active`, `.panel`, `.approval`, `.cap-grid`, `.mobile-nav`, `.kill`). Editorial
aesthetic: Cormorant Garamond display + IBM Plex Sans/Mono.

## Two-eras summary

**Modern** (keep/extend): runtime-console, trust, primitives, typed `atlasApi`, contracts,
Task Workspace. **Legacy** (retrofit): `app/dashboard/page.tsx` (raw fetch, `:8000`,
`any`), Home fakes, untyped `requestJSON` clients. Pass-5 work = unify onto the modern layer.
