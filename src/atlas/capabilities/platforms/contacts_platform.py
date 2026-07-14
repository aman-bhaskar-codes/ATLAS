"""Contacts Platform — read/search/list_all are Tier-1; create/update are Tier-2.

WHY ContactsPlatform owns KnownContacts: both email-send (6.5) and event-invite (6.6)
escalate when a recipient/attendee is NOT known. The known-contacts predicate must have
ONE owner and ONE sync path. sync_known() rebuilds the canonical KnownContacts instance
from the provider; app.py feeds the SAME instance to EmailPlatform and CalendarPlatform
after calling sync_known(), so 'known' is consistent across every outbound capability.
"""
from __future__ import annotations

from atlas.capabilities.domain.contacts import Contact, ContactDraft, KnownContacts
from atlas.capabilities.errors import CapabilityDenied
from atlas.capabilities.notification.domain.models import ApprovalRequest
from atlas.capabilities.notification.platform import NotificationPlatform
from atlas.capabilities.providers.contacts.base import ContactsProvider
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger

_log = get_logger("atlas.contacts")


class ContactsPlatform:
    def __init__(
        self, *, provider: ContactsProvider, notifications: NotificationPlatform,
        ids: IdGenerator, approval_channels: tuple[str, ...],
        seed: set[str] | None = None,
    ) -> None:
        self._provider = provider
        self._notify = notifications
        self._ids = ids
        self._approval_channels = approval_channels
        self._seed = seed or set()
        self._known = KnownContacts(self._seed)

    # ---- reads (Tier-1) ----------------------------------------------------
    async def search(self, query: str, *, limit: int = 20) -> list[Contact]:
        return await self._provider.search(query, limit=limit)

    async def get(self, contact_id: str) -> Contact:
        return await self._provider.get(contact_id)

    async def list_all(self, *, limit: int = 2000) -> list[Contact]:
        return await self._provider.list_all(limit=limit)

    # ---- known-contacts sync (the canonical updater) -----------------------
    async def sync_known(self) -> KnownContacts:
        """Rebuild the platform-wide known-contacts predicate from the provider.
        WHY here: KnownContacts must have one owner. Email-send (6.5) and event-invite
        (6.6) both read the SAME set, so 'known' can't drift between capabilities.
        Call this on startup and periodically thereafter."""
        contacts = await self._provider.list_all(limit=2000)
        addrs = {str(e.address) for c in contacts for e in c.emails}
        addrs |= self._seed          # always-known addresses from config
        self._known = KnownContacts(addrs)
        _log.info("contacts.known_synced", count=len(addrs))
        return self._known

    @property
    def known(self) -> KnownContacts:
        return self._known

    # ---- writes (Tier-2, previewed, human-approved) ------------------------
    async def create(self, draft: ContactDraft, correlation_id: CorrelationId) -> str:
        preview = self._render_preview(draft, "create")
        req = ApprovalRequest(
            id=self._ids.execution_id(), correlation_id=correlation_id,
            prompt=f"Create contact '{draft.name}'?",
            detail=preview, timeout_s=300.0, default_on_timeout=False)
        decision = await self._notify.request_approval(req, self._approval_channels)
        if not decision.approved:
            _log.info("contacts.create_denied", correlation_id=str(correlation_id),
                      timed_out=decision.timed_out)
            raise CapabilityDenied("contact create not approved"
                                   + (" (timed out)" if decision.timed_out else ""))
        contact_id = await self._provider.create(draft)
        # eagerly add to known set so subsequent outbound gates see the new contact
        for e in draft.emails:
            self._known.add(str(e.address))
        _log.info("contacts.created", correlation_id=str(correlation_id),
                  contact_id=contact_id)
        return contact_id

    async def update(self, draft: ContactDraft, correlation_id: CorrelationId) -> str:
        preview = self._render_preview(draft, "update")
        req = ApprovalRequest(
            id=self._ids.execution_id(), correlation_id=correlation_id,
            prompt=f"Update contact '{draft.name}'?",
            detail=preview, timeout_s=300.0, default_on_timeout=False)
        decision = await self._notify.request_approval(req, self._approval_channels)
        if not decision.approved:
            _log.info("contacts.update_denied", correlation_id=str(correlation_id),
                      timed_out=decision.timed_out)
            raise CapabilityDenied("contact update not approved"
                                   + (" (timed out)" if decision.timed_out else ""))
        contact_id = await self._provider.update(draft)
        _log.info("contacts.updated", correlation_id=str(correlation_id),
                  contact_id=contact_id)
        return contact_id

    def _render_preview(self, draft: ContactDraft, action: str) -> str:
        lines = [
            f"─── CONTACT {action.upper()} PREVIEW ───",
            f"Name:   {draft.name or '(no name)'}",
        ]
        if draft.emails:
            lines.append("Emails: " + ", ".join(str(e.address) for e in draft.emails))
        if draft.phones:
            lines.append("Phones: " + ", ".join(p.number for p in draft.phones))
        if draft.org:
            lines.append(f"Org:    {draft.org}")
            if draft.title:
                lines.append(f"Title:  {draft.title}")
        if draft.contact_id:
            lines.append(f"Contact ID: {draft.contact_id}")
        return "\n".join(lines)
