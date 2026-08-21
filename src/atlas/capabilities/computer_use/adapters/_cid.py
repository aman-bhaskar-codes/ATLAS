"""Correlation-id plumbing for substrate adapters.

WHY a reserved argument key: ControlAction is substrate-neutral, but engine
calls (Playwright, etc.) need a CorrelationId for audit trails. The engine
places it under ``arguments["correlation_id"]``; adapters reuse it or mint one.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from atlas.infra.ids import CorrelationId

CORRELATION_ID_KEY = "correlation_id"


def correlation_id_of(arguments: Mapping[str, Any]) -> CorrelationId:
    raw = arguments.get(CORRELATION_ID_KEY)
    if isinstance(raw, str) and raw:
        return CorrelationId(raw)
    return CorrelationId(uuid.uuid4().hex)
