"""Download Artifacts models."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from atlas.capabilities.domain.common import Provenance


class ArtifactKind(StrEnum):
    PDF = "pdf"
    CSV = "csv"
    IMAGE = "image"
    ZIP = "zip"
    HTML = "html"
    OTHER = "other"

class DownloadArtifact(BaseModel):
    """A download is a FIRST-CLASS artifact: hashed, provenance-tagged, memory-ready."""
    model_config = {"frozen": True}
    id: str
    filename: str
    kind: ArtifactKind
    size_bytes: int
    sha256: str
    sandbox_path: str
    source_url: str
    provenance: Provenance
    captured_ts: datetime
