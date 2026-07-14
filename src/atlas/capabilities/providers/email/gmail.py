"""Gmail provider — Gmail REST API, OAuth2 token from the Identity Platform.

WHY token via vault: ADR-016. authenticate() proves a token can be obtained;
every call fetches a fresh one (refresh handled inside 6.2). Gmail's nested
payload/parts are flattened to EmailMessage HERE and never leak upward.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from atlas.capabilities.domain.email import (
    EmailAddress, EmailDraft, EmailMessage, Thread,
)
from atlas.capabilities.errors import ProviderAuthError, ProviderExecutionError
from atlas.capabilities.identity.platform import IdentityPlatform

_API = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailProvider:
    name = "gmail"
    requires_auth = True

    def __init__(self, identity: IdentityPlatform, credential_id: str, timeout_s: float = 30.0) -> None:
        self._identity = identity
        self._credential_id = credential_id
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def initialize(self) -> None: ...

    async def authenticate(self) -> None:
        await self._identity.get_usable_secret(self._credential_id)

    async def _headers(self) -> dict[str, str]:
        try:
            token = await self._identity.get_usable_secret(self._credential_id)
        except Exception as exc:
            raise ProviderAuthError(f"gmail token unavailable: {exc}") from exc
        return {"Authorization": f"Bearer {token}"}

    async def health(self) -> bool:
        try:
            await self._identity.get_usable_secret(self._credential_id)
            return True
        except Exception:
            return False

    async def search(self, query: str, *, limit: int) -> list[EmailMessage]:
        h = await self._headers()
        r = await self._client.get(f"{_API}/messages", headers=h,
                                   params={"q": query, "maxResults": limit})
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("messages", [])]
        out: list[EmailMessage] = []
        for mid in ids:
            mr = await self._client.get(f"{_API}/messages/{mid}", headers=h,
                                        params={"format": "full"})
            mr.raise_for_status()
            out.append(self._to_message(mr.json()))
        return out

    async def get_thread(self, thread_id: str) -> Thread:
        h = await self._headers()
        r = await self._client.get(f"{_API}/threads/{thread_id}", headers=h,
                                   params={"format": "full"})
        r.raise_for_status()
        data = r.json()
        msgs = tuple(self._to_message(m) for m in data.get("messages", []))
        return Thread(id=thread_id, subject=msgs[0].subject if msgs else "", messages=msgs)

    async def send(self, draft: EmailDraft) -> str:
        h = await self._headers()
        raw = base64.urlsafe_b64encode(self._build_mime(draft).encode()).decode()
        body: dict[str, Any] = {"raw": raw}
        if draft.thread_id:
            body["threadId"] = draft.thread_id
        try:
            r = await self._client.post(f"{_API}/messages/send", headers=h, json=body)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderExecutionError(f"gmail send failed: {exc}") from exc
        return str(r.json()["id"])

    def _build_mime(self, draft: EmailDraft) -> str:
        # minimal RFC822; attachments via multipart handled in full impl
        lines = [f"To: {', '.join(a.render() for a in draft.to)}"]
        if draft.cc:
            lines.append(f"Cc: {', '.join(a.render() for a in draft.cc)}")
        if draft.bcc:
            lines.append(f"Bcc: {', '.join(a.render() for a in draft.bcc)}")
        lines += [f"Subject: {draft.subject}", "Content-Type: text/plain; charset=utf-8", "",
                  draft.body_text]
        return "\r\n".join(lines)

    def _to_message(self, data: dict[str, Any]) -> EmailMessage:
        headers = {h["name"].lower(): h["value"] for h in
                   data.get("payload", {}).get("headers", [])}
        return EmailMessage(
            id=data["id"], thread_id=data.get("threadId"),
            sender=_parse_addr(headers.get("from", "")),
            to=tuple(_parse_addr(a) for a in headers.get("to", "").split(",") if a.strip()),
            cc=tuple(_parse_addr(a) for a in headers.get("cc", "").split(",") if a.strip()),
            subject=headers.get("subject", ""), snippet=data.get("snippet", ""),
            body_text=_extract_body(data.get("payload", {})),
            labels=tuple(data.get("labelIds", [])),
            unread="UNREAD" in data.get("labelIds", []))

    async def shutdown(self) -> None:
        await self._client.aclose()


def _parse_addr(raw: str) -> EmailAddress:
    raw = raw.strip()
    if not raw:
        return EmailAddress(email="unknown@unknown.local")
    if "<" in raw and ">" in raw:
        name = raw.split("<")[0].strip().strip('"')
        email = raw.split("<")[1].split(">")[0].strip()
        return EmailAddress(email=email, name=name or None) # type: ignore
    return EmailAddress(email=raw) # type: ignore


def _extract_body(payload: dict[str, Any]) -> str:
    import base64 as b64
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return b64.urlsafe_b64decode(payload["body"]["data"]).decode(errors="replace")
    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text
    return ""
