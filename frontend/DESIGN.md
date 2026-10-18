---
version: alpha
name: ATLAS Command Center
description: A local agent operations console with inspectable runtime state, dense records, and restrained accent cues.
colors:
  background: "oklch(10% 0.018 278)"
  surface: "oklch(14% 0.024 278)"
  border: "oklch(34% 0.035 278)"
  text: "oklch(94% 0.012 82)"
  muted: "oklch(62% 0.025 82)"
  accent: "oklch(58% 0.16 292)"
  success: "oklch(73% 0.13 162)"
  danger: "oklch(68% 0.18 22)"
typography:
  sans:
    fontFamily: '"IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
  serif:
    fontFamily: '"Cormorant Garamond", ui-serif, Georgia, "Times New Roman", serif'
  mono:
    fontFamily: '"IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace'
rounded:
  xs: "4px"
  sm: "6px"
  md: "10px"
  lg: "14px"
  xl: "20px"
omitted:
  - section: spacing
    reason: Runtime spacing is density-scaled; styles/tokens.css is canonical rather than a second fixed scale.
components:
  auditTable:
    description: Read-only records, UTC timestamps, server filters, bounded cursor pages, and explicit export scope.
---

# ATLAS interface design

## Overview

ATLAS is a local-first personal agent runtime governed by a safety gate and a
tamper-evident audit log (repository README.md, infra/types.py, safety/audit.py).
This is a product console for reviewing execution and submitting requests. Its
identity comes from the existing dark ink surfaces, warm paper content, IBM Plex
utility typography, serif headings, and small accent/status cues. Preserve that
established language; this document records the implementation rather than a rebrand.

Runtime CSS remains authoritative. `styles/tokens.css` owns invariant scales;
`styles/themes.css` owns dark/light/high-contrast colors and accent variants;
`app/globals.css` maps those variables into Tailwind utilities. Frontmatter records
the default dark palette. Existing user theme, density, font-scale, and motion
preferences override defaults through those runtime files.

## Colors

Use semantic roles (`--surface-sunken`, `--surface-card`, `--border-subtle`,
`--text-primary`, `--text-secondary`, `--text-muted`, `--accent`) for new markup.
Legacy `ink`/`paper` ramps remain mapped by theme. Do not substitute literal dark
colors in new content. Jade indicates successful outcomes; danger indicates
denial/error. Every status also has a text label. Gold is an existing supplementary
cue, not proof that a service is healthy or selected.

## Typography

Body and controls use `--font-sans`; editorial headings use `--font-serif` with
restraint; identifiers/timestamps use `--font-mono`. Runtime text sizes scale with
`data-font-scale`. Keep actual values readable in tables and let long content wrap
or scroll within its owner. English is the current interface language; timestamps
on the audit page are explicitly UTC, independent of browser locale formatting.

## Layout

The command centre owns its persistent shell and focused component pages.
Research (`/research`, history, corpus, status, sessions) and the IDE workspace
launcher (`/workspaces`, with `/ide` alias) own separate platform navigation and
a visible Return to command centre link. `/dev/[workspaceId]` owns its workbench
chrome. Launch tiles open named same-origin tabs, detach the opener, and fall
back to the same window when blocked. Platform navigation wraps on mobile.
Keep sibling-page panel structure. Dense operational
tables own horizontal overflow; page controls wrap at narrow widths. No audit
table sizing may clip the composer or alter document scrolling. The spacing
rhythm is the existing density-scaled 2/4/8/16/24/32/48/64px scale from tokens.css.
Use those variables and existing Tailwind mappings rather than creating a second
layout system. Filters use native selects: platform-owned popup geometry and
keyboard behavior are accepted for these simple enumerated controls.

## Elevation & Depth

Reuse `--shadow-1`/`--shadow-2` through existing primitive styles when a raised
surface is warranted. Read-only records do not need decorative lift or motion.
Data loading and feedback should reserve space and keep controls stable.

## Shapes

Use the existing radius tokens. Preserve shell panels and composer borders;
new buttons reuse `components/primitives/Button.tsx`. Lucide is the existing icon
family. Icons supplement text instead of replacing names or status evidence.

## Components

`ErrorState`, `ErrorRow`, and `EmptyState` own feedback presentation.
Audit results use a semantic table with labeled native date/tier filters and
URL-restorable filter/cursor state. Date filtering is server-side across the log,
with inclusive UTC cutoffs; cursor pages contain at most 50 rows. Export requests
start at the newest matching event and omit raw payloads, at most 1,000 events.
Both the downloaded file and visible feedback state whether matches were omitted.

The composer context strip reports only draft context and observed capability,
memory, and MCP state. Missing/loading/error state must remain explicit. Memory
counts do not imply that memory is active for a particular task, and registered
capabilities do not imply a selected workspace or connected MCP server.

## Do's and Don'ts

- Preserve existing tokens, theme adapters, shell structure, and native semantics.
- Show observed counts, real outcomes, and precise scope for filters/exports.
- Use real buttons and visible focus, loading, disabled, error, and empty states.
- Do not use browser alerts or invented labels for workspace, memory, tier, or MCP.
- Do not introduce a marketing hero, new palette, or decorative animation into records.
