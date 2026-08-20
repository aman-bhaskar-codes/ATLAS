from __future__ import annotations

from pydantic import BaseModel

from atlas.capabilities.domain.common import Provenance


class ExchangeRate(BaseModel):
    model_config = {"frozen": True}
    base: str
    target: str
    rate: float
    date: str
    provenance: Provenance