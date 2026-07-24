"""Tests for ContactsPlatform and KnownContacts — the single source of truth."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.capabilities.domain.contacts import Contact, ContactDraft, EmailRef
from atlas.capabilities.errors import CapabilityDenied
from atlas.capabilities.notification.domain.models import ApprovalDecision, ApprovalRequest
from atlas.capabilities.platforms.contacts_platform import ContactsPlatform
from atlas.infra.ids import CorrelationId


class FakePeopleProvider:
    name = "fake_people"
    requires_auth = False

    def __init__(self, contacts: list[Contact]) -> None:
        self._contacts = contacts
        self.created: ContactDraft | None = None

    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool: return True
    async def search(self, query: str, *, limit: int) -> list[Contact]:
        return [c for c in self._contacts if query.lower() in c.name.lower()]
    async def get(self, contact_id: str) -> Contact:
        return next(c for c in self._contacts if c.id == contact_id)
    async def list_all(self, *, limit: int) -> list[Contact]:
        return self._contacts[:limit]
    async def create(self, draft: ContactDraft) -> str:
        self.created = draft
        return "people/new-001"
    async def update(self, draft: ContactDraft) -> str:
        return draft.contact_id or "people/upd-001"
    async def shutdown(self) -> None: ...


class ApproveNotify:
    def __init__(self, approved: bool) -> None:
        self._a = approved
        self.previewed: str | None = None
    async def request_approval(self, req: ApprovalRequest,
                               channels: tuple[str, ...]) -> ApprovalDecision:
        self.previewed = req.detail
        return ApprovalDecision(
            request_id=req.id, approved=self._a,
            decided_ts=datetime.now(UTC))


class FakeIds:
    def execution_id(self) -> str: return "e1"
    def correlation_id(self) -> CorrelationId: return CorrelationId("c")
    def task_id(self) -> str: return "t1"


_ALICE = Contact(id="people/c1", name="Alice",
                 emails=(EmailRef(address="alice@example.com", primary=True),))
_BOB = Contact(id="people/c2", name="Bob",
               emails=(EmailRef(address="bob@example.com", primary=True),))


def _platform(approved: bool = True,
              contacts: list[Contact] | None = None,
              seed: set[str] | None = None) -> tuple[ContactsPlatform, FakePeopleProvider, ApproveNotify]:
    people = FakePeopleProvider(contacts or [_ALICE, _BOB])
    notify = ApproveNotify(approved)
    platform = ContactsPlatform(
        provider=people, notifications=notify, ids=FakeIds(),  # type: ignore[arg-type]
        approval_channels=("ntfy:atlas",), seed=seed)
    return platform, people, notify


@pytest.mark.asyncio
async def test_sync_known_is_single_source_of_truth() -> None:
    """sync_known() populates a KnownContacts that both email + calendar read."""
    platform, _, _ = _platform()
    known = await platform.sync_known()

    # Both alice and bob should be known
    assert known.is_known("alice@example.com")
    assert known.is_known("bob@example.com")
    assert not known.is_known("unknown@stranger.com")


@pytest.mark.asyncio
async def test_sync_known_is_case_insensitive() -> None:
    platform, _, _ = _platform()
    known = await platform.sync_known()
    assert known.is_known("ALICE@EXAMPLE.COM")
    assert known.is_known("Alice@Example.Com")


@pytest.mark.asyncio
async def test_sync_known_includes_seed() -> None:
    """Seed addresses are always known even if not returned by the provider."""
    platform, _, _ = _platform(contacts=[], seed={"admin@corp.com"})
    known = await platform.sync_known()
    assert known.is_known("admin@corp.com")


@pytest.mark.asyncio
async def test_create_approved_calls_provider_and_updates_known() -> None:
    platform, people, _ = _platform(approved=True)
    await platform.sync_known()
    draft = ContactDraft(name="Charlie", emails=(EmailRef(address="charlie@new.com"),))
    cid = await platform.create(draft, CorrelationId("c"))
    assert cid == "people/new-001"
    assert people.created is draft
    # charlie should now be in the known set (eagerly added after create)
    assert platform.known.is_known("charlie@new.com")


@pytest.mark.asyncio
async def test_create_denied_raises_capability_denied() -> None:
    platform, people, _ = _platform(approved=False)
    draft = ContactDraft(name="Spammer", emails=(EmailRef(address="spam@evil.com"),))
    with pytest.raises(CapabilityDenied):
        await platform.create(draft, CorrelationId("c"))
    assert people.created is None


@pytest.mark.asyncio
async def test_search_is_tier1_no_approval_needed() -> None:
    """Search must work without any approval interaction."""
    platform, _, notify = _platform()
    results = await platform.search("Alice", limit=5)
    assert len(results) == 1
    assert results[0].name == "Alice"
    assert notify.previewed is None  # no approval triggered
