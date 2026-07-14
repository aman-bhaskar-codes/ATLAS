"""Email Platform — read/search/compose are easy; SEND is the guarded path.

SEND PIPELINE (the reason this phase exists):
  1. classify recipients: any unknown contact? -> force the strongest gate
  2. build a REAL preview (actual to/cc/subject/body/attachment names)
  3. dispatch send as a Tier-2 capability through the Safety Engine, whose
     confirmation is delivered via the 6.4 approval path (preview shown on your
     phone) — nothing sends until you approve
  4. only on approval does the provider actually send
Compose/reply/forward are Tier-0 (produce an EmailDraft, no side effect).
"""

from __future__ import annotations

from atlas.capabilities.domain.contacts import KnownContacts
from atlas.capabilities.domain.email import (
    Contact,
    EmailDraft,
    EmailMessage,
    Thread,
)
from atlas.capabilities.errors import CapabilityDenied
from atlas.capabilities.notification.domain.models import ApprovalRequest
from atlas.capabilities.notification.platform import NotificationPlatform
from atlas.capabilities.providers.email.base import EmailProvider
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger

_log = get_logger("atlas.email")


class EmailPlatform:
    def __init__(
        self, *, provider: EmailProvider, notifications: NotificationPlatform,
        ids: IdGenerator, known_contacts: set[str], approval_channels: tuple[str, ...],
    ) -> None:
        self._provider = provider
        self._notify = notifications
        self._ids = ids
        self._known_obj: KnownContacts | None = None
        self._known = {c.lower() for c in known_contacts}
        self._approval_channels = approval_channels

    def set_known_contacts(self, known: KnownContacts) -> None:
        """Replace the ad-hoc known set with the canonical ContactsPlatform-managed set.
        Called from app.py after contacts_platform.sync_known() so all outbound
        gates read the SAME KnownContacts instance."""
        self._known_obj = known

    def _is_known(self, email: str) -> bool:
        if self._known_obj is not None:
            return self._known_obj.is_known(email)
        return email.lower() in self._known

    # ---- reads (Tier-1) -------------------------------------------------
    async def search(self, query: str, *, limit: int = 20) -> list[EmailMessage]:
        return await self._provider.search(query, limit=limit)

    async def get_thread(self, thread_id: str) -> Thread:
        return await self._provider.get_thread(thread_id)

    # ---- compose (Tier-0, no side effect) -------------------------------
    def classify_recipients(self, draft: EmailDraft) -> list[Contact]:
        return [Contact(address=a.email, name=a.name,
                        known=self._is_known(f"{a.email}"))
                for a in draft.all_recipients()]

    # ---- SEND (Tier-2, previewed, human-approved) -----------------------
    async def send(self, draft: EmailDraft, correlation_id: CorrelationId) -> str:
        contacts = self.classify_recipients(draft)
        unknown = [c for c in contacts if not c.known]
        preview = self._render_preview(draft, unknown)

        # Approval via the 6.4 platform. Unknown recipients => stronger warning,
        # deny-on-timeout. This is the highest-regret action, so default is NO.
        req = ApprovalRequest(
            id=self._ids.execution_id(), correlation_id=correlation_id,
            prompt=("Send this email?" if not unknown
                    else f"⚠️ Send to {len(unknown)} NEW contact(s)?"),
            detail=preview, timeout_s=600.0, default_on_timeout=False)
        decision = await self._notify.request_approval(req, self._approval_channels)
        if not decision.approved:
            _log.info("email.send_denied", event_type="email",
                      correlation_id=correlation_id, timed_out=decision.timed_out,
                      unknown_recipients=len(unknown))
            raise CapabilityDenied(
                "send not approved" + (" (timed out)" if decision.timed_out else ""))

        message_id = await self._provider.send(draft)
        _log.info("email.sent", event_type="email", correlation_id=correlation_id,
                  message_id=message_id, recipients=len(contacts))
        return message_id

    def _render_preview(self, draft: EmailDraft, unknown: list[Contact]) -> str:
        """The EXACT outbound, not a paraphrase. This is what the user approves."""
        lines = [
            "─── EMAIL PREVIEW ───",
            f"To:      {', '.join(a.render() for a in draft.to)}",
        ]
        if draft.cc:
            lines.append(f"Cc:      {', '.join(a.render() for a in draft.cc)}")
        if draft.bcc:
            lines.append(f"Bcc:     {', '.join(a.render() for a in draft.bcc)}")
        lines += [f"Subject: {draft.subject}", ""]
        if unknown:
            lines.append("⚠️ NEW CONTACTS (not in your known list): "
                         + ", ".join(f"{c.address}" for c in unknown))
            lines.append("")
        lines.append(draft.body_text)
        if draft.attachments:
            lines.append("")
            lines.append("Attachments: " + ", ".join(a.filename for a in draft.attachments))
        return "\n".join(lines)
