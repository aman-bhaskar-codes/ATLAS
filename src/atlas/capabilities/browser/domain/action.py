"""A BrowserAction is the unit the Safety Engine classifies. WHY typed kinds: the
classifier tiers by kind + target (a click on a 'Buy' button is financial_ui;
typing into a password field is credential_entry; a form submit reaches an external
system). The preview renders from ActionPreview so the human approves the ACTUAL act.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from atlas.capabilities.browser.domain.locator import Locator
from atlas.capabilities.browser.domain.page import PageHandle, PageState


class ActionKind(StrEnum):
    NAVIGATE = "navigate"        # Tier-1
    CLICK = "click"              # Tier-1..2 (destructive_ui/financial_ui escalate)
    TYPE = "type"                # Tier-1..2 (credential_entry escalates)
    SUBMIT = "submit"            # Tier-2 ALWAYS (form reaches a system) — preview gate
    UPLOAD = "upload"            # Tier-2
    DOWNLOAD = "download"        # Tier-2
    DIALOG_ACCEPT = "dialog_accept"  # Tier-2
    SCROLL = "scroll"            # Tier-0
    EXTRACT = "extract"          # Tier-0/1
    SCREENSHOT = "screenshot"    # Tier-1 (sensitive-app blocklist applies)

class ActionPreview(BaseModel):
    """The human-approved rendering of a mutating action. For SUBMIT this lists the
    exact field→value pairs (secrets redacted to '••••') going out."""
    model_config = {"frozen": True}
    summary: str
    target_description: str = ""
    field_values: tuple[tuple[str, str], ...] = ()   # (label, value|redacted)
    url: str = ""
    warnings: tuple[str, ...] = ()                    # new-domain, financial, external-recipient

class BrowserAction(BaseModel):
    model_config = {"frozen": True}
    handle: PageHandle
    kind: ActionKind
    locator: Locator | None = None
    value: str | None = None            # text to type, url to navigate, path to upload
    args: dict[str, Any] = {}

class ActionResult(BaseModel):
    model_config = {"frozen": True}
    ok: bool
    action: BrowserAction
    post_state: PageState | None = None
    error: str | None = None
    latency_ms: int = 0
