"""SUPERSEDED — retired in Pass 1 (Phase 2).

The old ``Router`` made a per-task model call to classify a request's
capabilities. That is now a deterministic, zero-model-call projection:
``atlas.orchestration.understanding.capabilities_from_intent`` derives the same
``Capabilities`` from the already-extracted ``TaskIntent``, so we neither pay a
second model round-trip nor risk the classifier disagreeing with the intent.

The ``Router`` class is intentionally gone (not left as disconnected code). This
file remains only because ``rm`` was unavailable when it was retired; delete it
once the shell is available again. Nothing imports it.
"""

from __future__ import annotations
