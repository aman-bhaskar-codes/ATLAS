"""Research surface (Phase 1) — a governed, persisted Perplexity-class research
use-case.

This package composes the EXISTING governed research pipeline (the ``knowledge``
tool → ``SafetyEngine.guard`` → knowledge fabric / research runner / supervisor)
into a durable, resumable SESSION surface: a question in, a grounded, cited answer
+ source rail out, persisted and addressable. It adds ONLY orchestration,
persistence, and (later) streaming — never a second execution path and never a
second retrieval system (Constitution: one funnel, one fabric).
"""
