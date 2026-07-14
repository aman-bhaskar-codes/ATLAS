from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class LocatorKind(StrEnum):
    ROLE = "role"
    TEXT = "text"
    LABEL = "label"
    ARIA = "aria"
    CSS = "css"
    XPATH = "xpath"
    VISUAL = "visual"   # visual = future grounding

class Locator(BaseModel):
    """Provider-neutral element addressing. The DOM/Locator engine resolves this to
    whatever the provider needs; callers NEVER write page.locator('div.x > a')."""
    model_config = {"frozen": True}
    kind: LocatorKind
    value: str                       # role name / text / css / xpath
    name: str | None = None          # accessible name for ROLE
    nth: int | None = None
    exact: bool = False
