"""Contacts domain models — provider-neutral, and the canonical known-contacts set.

WHY KnownContacts lives here (not in email): both email-send (6.5) and event-invite
(6.6) escalate when a recipient/attendee is NOT known. That predicate must have ONE
definition. The ContactsPlatform owns it; email's thin known set is populated FROM
here so 'known' is consistent across every outbound capability.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, EmailStr


class ContactLabel(StrEnum):
    HOME = "home"
    WORK = "work"
    OTHER = "other"


class EmailRef(BaseModel):
    model_config = {"frozen": True}
    address: EmailStr
    label: ContactLabel = ContactLabel.OTHER
    primary: bool = False


class PhoneNumber(BaseModel):
    model_config = {"frozen": True}
    number: str
    label: ContactLabel = ContactLabel.OTHER


class Contact(BaseModel):
    model_config = {"frozen": True}
    id: str
    name: str = ""
    emails: tuple[EmailRef, ...] = ()
    phones: tuple[PhoneNumber, ...] = ()
    org: str | None = None
    title: str | None = None
    notes: str = ""

    def primary_email(self) -> str | None:
        for e in self.emails:
            if e.primary:
                return str(e.address)
        return str(self.emails[0].address) if self.emails else None


class ContactDraft(BaseModel):
    """Intended create/update. Rendered in the preview, approved before commit."""
    model_config = {"frozen": True}
    name: str = ""
    emails: tuple[EmailRef, ...] = ()
    phones: tuple[PhoneNumber, ...] = ()
    org: str | None = None
    title: str | None = None
    contact_id: str | None = None         # None => create; set => update


class KnownContacts:
    """The one predicate both email-send and event-invite consult.
    Case-insensitive on the email address. Populated from the ContactsPlatform
    (and optionally seeded from config)."""
    def __init__(self, addresses: set[str]) -> None:
        self._known = {a.lower() for a in addresses}

    def is_known(self, email: str) -> bool:
        return email.lower() in self._known

    def add(self, email: str) -> None:
        self._known.add(email.lower())

    def snapshot(self) -> set[str]:
        return set(self._known)
