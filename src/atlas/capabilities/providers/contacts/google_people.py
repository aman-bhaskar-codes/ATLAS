"""Google People provider — People API, OAuth2 token from Identity Platform.

WHY: Contacts are the source of truth for the known-contacts set that gates email
sends (6.5) and event invites (6.6). This adapter normalizes Google's person
resource to the Contact domain model; no Google shape escapes the adapter.

Key mapping:
  - resourceName (people/c123) → Contact.id
  - names[0].displayName → name
  - emailAddresses[].value → EmailRef (primary if metadata.primary)
  - phoneNumbers[].value → PhoneNumber
  - organizations[0].name/title → org/title
"""
from __future__ import annotations

import httpx

from atlas.capabilities.domain.contacts import Contact, ContactDraft, ContactLabel, EmailRef, PhoneNumber
from atlas.capabilities.errors import ProviderAuthError, ProviderExecutionError
from atlas.capabilities.identity.platform import IdentityPlatform

_API = "https://people.googleapis.com/v1"
_READ_MASK = "names,emailAddresses,phoneNumbers,organizations,biographies"


class GooglePeopleProvider:
    name = "google_people"
    requires_auth = True

    def __init__(self, identity: IdentityPlatform, credential_id: str,
                 timeout_s: float = 30.0) -> None:
        self._identity = identity
        self._credential_id = credential_id
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def initialize(self) -> None:
        pass

    async def authenticate(self) -> None:
        await self._identity.get_usable_secret(self._credential_id)

    async def _headers(self) -> dict[str, str]:
        try:
            token = await self._identity.get_usable_secret(self._credential_id)
        except Exception as exc:
            raise ProviderAuthError(f"people token unavailable: {exc}") from exc
        return {"Authorization": f"Bearer {token}"}

    async def health(self) -> bool:
        try:
            await self._identity.get_usable_secret(self._credential_id)
            return True
        except Exception:
            return False

    async def search(self, query: str, *, limit: int) -> list[Contact]:
        h = await self._headers()
        r = await self._client.get(
            f"{_API}/people:searchContacts", headers=h,
            params={"query": query, "readMask": _READ_MASK, "pageSize": limit})
        r.raise_for_status()
        return [self._to_contact(p["person"]) for p in r.json().get("results", [])
                if "person" in p]

    async def get(self, contact_id: str) -> Contact:
        h = await self._headers()
        r = await self._client.get(
            f"{_API}/{contact_id}", headers=h,
            params={"personFields": _READ_MASK})
        r.raise_for_status()
        return self._to_contact(r.json())

    async def list_all(self, *, limit: int) -> list[Contact]:
        """Page through all connections — used for known-contacts sync."""
        h = await self._headers()
        contacts: list[Contact] = []
        page_token: str | None = None
        while True:
            params: dict[str, str | int] = {
                "personFields": _READ_MASK,
                "pageSize": min(limit - len(contacts), 1000),
            }
            if page_token:
                params["pageToken"] = page_token
            r = await self._client.get(
                f"{_API}/people/me/connections", headers=h, params=params)
            r.raise_for_status()
            data = r.json()
            contacts.extend(self._to_contact(p) for p in data.get("connections", []))
            page_token = data.get("nextPageToken")
            if not page_token or len(contacts) >= limit:
                break
        return contacts[:limit]

    async def create(self, draft: ContactDraft) -> str:
        h = await self._headers()
        body = self._to_body(draft)
        try:
            r = await self._client.post(f"{_API}/people:createContact",
                                        headers=h, json=body)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderExecutionError(f"people create failed: {exc}") from exc
        return str(r.json().get("resourceName", ""))

    async def update(self, draft: ContactDraft) -> str:
        if not draft.contact_id:
            raise ProviderExecutionError("update requires contact_id")
        h = await self._headers()
        body = self._to_body(draft)
        update_mask = "names,emailAddresses,phoneNumbers,organizations"
        try:
            r = await self._client.patch(
                f"{_API}/{draft.contact_id}:updateContact",
                headers=h, params={"updatePersonFields": update_mask}, json=body)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderExecutionError(f"people update failed: {exc}") from exc
        return str(r.json().get("resourceName", ""))

    async def shutdown(self) -> None:
        await self._client.aclose()

    # ---- mapping (the ONLY place Google's People shape exists) ---------------
    def _to_contact(self, person: dict[str, object]) -> Contact:
        name = ""
        names = person.get("names") or []
        if isinstance(names, list) and names:
            name = str(names[0].get("displayName", "")) if isinstance(names[0], dict) else ""

        emails: list[EmailRef] = []
        for ea in (person.get("emailAddresses") or []):
            if not isinstance(ea, dict):
                continue
            val = str(ea.get("value", ""))
            if not val:
                continue
            meta = ea.get("metadata") or {}
            is_primary = bool(isinstance(meta, dict) and meta.get("primary"))
            emails.append(EmailRef(address=val, primary=is_primary,
                                   label=ContactLabel.WORK if is_primary else ContactLabel.OTHER))

        phones: list[PhoneNumber] = []
        for ph in (person.get("phoneNumbers") or []):
            if not isinstance(ph, dict):
                continue
            val = str(ph.get("value", ""))
            if val:
                phones.append(PhoneNumber(number=val))

        org: str | None = None
        title: str | None = None
        orgs = person.get("organizations") or []
        if isinstance(orgs, list) and orgs and isinstance(orgs[0], dict):
            org = str(orgs[0].get("name", "")) or None
            title = str(orgs[0].get("title", "")) or None

        bios = person.get("biographies") or []
        notes = ""
        if isinstance(bios, list) and bios and isinstance(bios[0], dict):
            notes = str(bios[0].get("value", ""))

        return Contact(
            id=str(person.get("resourceName", "")),
            name=name,
            emails=tuple(emails),
            phones=tuple(phones),
            org=org,
            title=title,
            notes=notes,
        )

    def _to_body(self, draft: ContactDraft) -> dict[str, object]:
        body: dict[str, object] = {}
        if draft.name:
            body["names"] = [{"displayName": draft.name, "givenName": draft.name}]
        if draft.emails:
            body["emailAddresses"] = [{"value": str(e.address)} for e in draft.emails]
        if draft.phones:
            body["phoneNumbers"] = [{"value": p.number} for p in draft.phones]
        if draft.org or draft.title:
            body["organizations"] = [{"name": draft.org or "", "title": draft.title or ""}]
        return body
