"""Email domain models — provider-neutral.

WHY EmailDraft is distinct from EmailMessage: a Draft is what we intend to send
(the thing the preview shows and the user approves); a Message is what exists in
the mailbox. Keeping them separate makes 'compose' (Tier-0, no side effect) and
'send' (Tier-2, irreversible) cleanly different operations.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr


class EmailAddress(BaseModel):
    model_config = {"frozen": True}
    email: EmailStr
    name: str | None = None

    def render(self) -> str:
        return f"{self.name} <{self.email}>" if self.name else f"{self.email}"


class Attachment(BaseModel):
    model_config = {"frozen": True}
    filename: str
    mime_type: str
    size_bytes: int
    content_id: str | None = None          # for referencing stored bytes (File Gateway, 6.8)


class EmailMessage(BaseModel):
    model_config = {"frozen": True}
    id: str
    thread_id: str | None = None
    sender: EmailAddress
    to: tuple[EmailAddress, ...] = ()
    cc: tuple[EmailAddress, ...] = ()
    subject: str = ""
    snippet: str = ""
    body_text: str = ""
    date: datetime | None = None
    labels: tuple[str, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    unread: bool = False


class Thread(BaseModel):
    model_config = {"frozen": True}
    id: str
    subject: str = ""
    messages: tuple[EmailMessage, ...] = ()


class EmailDraft(BaseModel):
    """The intended outbound. This is what the preview renders and the user
    approves. reply_to_id/thread_id set for replies/forwards."""
    model_config = {"frozen": True}
    to: tuple[EmailAddress, ...]
    cc: tuple[EmailAddress, ...] = ()
    bcc: tuple[EmailAddress, ...] = ()
    subject: str = ""
    body_text: str = ""
    attachments: tuple[Attachment, ...] = ()
    reply_to_id: str | None = None
    thread_id: str | None = None

    def all_recipients(self) -> tuple[EmailAddress, ...]:
        return self.to + self.cc + self.bcc


class Contact(BaseModel):
    model_config = {"frozen": True}
    address: EmailStr
    name: str | None = None
    known: bool = False                     # in the user's known-contacts set
