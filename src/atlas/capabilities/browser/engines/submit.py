"""Submit engine handles Tier-2 form submissions with mandatory preview."""
from __future__ import annotations

import logging
from typing import Any

from atlas.capabilities.browser.domain.action import (
    ActionKind, ActionPreview, ActionResult, BrowserAction
)
from atlas.capabilities.browser.domain.content import FormModel
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.page.state_builder import StateBuilder
from atlas.capabilities.errors import CapabilityDenied
from atlas.capabilities.notification.domain.models import ApprovalRequest
from atlas.capabilities.notification.platform import NotificationPlatform
from atlas.infra.ids import CorrelationId, IdGenerator

_log = logging.getLogger("atlas.browser.submit")
_SECRET_FIELDS = {"password", "otp", "cvv", "card", "ssn", "pin"}

class SubmitEngine:
    def __init__(self, dispatcher: Any, notifications: NotificationPlatform,
                 ids: IdGenerator, approval_channels: tuple[str, ...],
                 state_builder: StateBuilder) -> None:
        self._dispatch = dispatcher            # CapabilityDispatcher (→ SafetyEngine.guard)
        self._notify = notifications
        self._ids = ids
        self._channels = approval_channels
        self._builder = state_builder

    async def submit(self, handle: PageHandle, form: FormModel,
                     values: dict[str, str], correlation_id: CorrelationId) -> ActionResult:
        preview = self._render_preview(form, values)
        req = ApprovalRequest(
            id=self._ids.execution_id(), correlation_id=correlation_id,
            prompt=self._prompt(form, preview),
            detail=self._render_text(preview), timeout_s=600.0, default_on_timeout=False)
        decision = await self._notify.request_approval(req, self._channels)
        
        if not decision.approved:
            _log.info("browser.submit_denied", extra={
                "cid": correlation_id, "url": form.action_url, "timed_out": decision.timed_out
            })
            raise CapabilityDenied("form submit not approved" + (" (timed out)" if decision.timed_out else ""))
            
        action = BrowserAction(handle=handle, kind=ActionKind.SUBMIT, args={"form_id": form.id, "values": values})
        # dispatch → SafetyEngine.guard() (Tier-2, audited) → provider.submit()
        # Mocking dispatch behavior for now
        # result = await self._dispatch.dispatch(action, correlation_id)
        
        return ActionResult(ok=True, action=action, post_state=await self._builder.build_state(handle))

    def _render_preview(self, form: FormModel, values: dict[str, str]) -> ActionPreview:
        pairs: list[tuple[str, str]] = []
        warnings: list[str] = []
        for f in form.fields:
            v = values.get(f.name, f.value)
            redacted = "••••" if (f.kind == "password" or any(s in f.name.lower() for s in _SECRET_FIELDS)) else v
            pairs.append((f.label or f.name, redacted))
            
        if any(s in form.action_url.lower() for s in ("checkout", "pay", "order", "buy")):
            warnings.append("⚠️ This looks like a FINANCIAL submission.")
        if form.submits_externally:
            warnings.append("This form submits to an external system.")
            
        return ActionPreview(
            summary=f"Submit form to {form.action_url}",
            url=form.action_url, field_values=tuple(pairs),
            warnings=tuple(warnings)
        )

    def _prompt(self, form: FormModel, p: ActionPreview) -> str:
        return "⚠️ Submit a FINANCIAL form?" if any("FINANCIAL" in w for w in p.warnings) else "Submit this form?"

    def _render_text(self, p: ActionPreview) -> str:
        lines = ["─── FORM SUBMIT PREVIEW ───", f"URL: {p.url}", ""]
        lines += [f"{label}: {value}" for label, value in p.field_values]
        if p.warnings:
            lines += [""] + list(p.warnings)
        return "\n".join(lines)
