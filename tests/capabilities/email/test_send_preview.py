from datetime import UTC, datetime

import pytest

from atlas.capabilities.domain.email import EmailAddress, EmailDraft, EmailMessage, Thread
from atlas.capabilities.errors import CapabilityDenied
from atlas.capabilities.notification.domain.models import ApprovalDecision, ApprovalRequest
from atlas.capabilities.platforms.email_platform import EmailPlatform
from atlas.infra.ids import CorrelationId


class FakeProvider:
    name = "fake"
    requires_auth = True
    def __init__(self) -> None:
        self.sent: EmailDraft | None = None
        
    async def initialize(self) -> None: ...
    async def authenticate(self) -> None: ...
    async def health(self) -> bool: return True
    async def search(self, query: str, *, limit: int) -> list[EmailMessage]: return []
    async def get_thread(self, thread_id: str) -> Thread: raise NotImplementedError
    async def send(self, draft: EmailDraft) -> str:
        self.sent = draft
        return "msg-1"
    async def shutdown(self) -> None: ...


class ApproveNotify:
    def __init__(self, approved: bool) -> None:
        self._a = approved
        self.previewed: str | None = None
        
    async def request_approval(self, req: ApprovalRequest, channels: tuple[str, ...]) -> ApprovalDecision:
        self.previewed = req.detail
        return ApprovalDecision(request_id=req.id, approved=self._a,
                                decided_ts=datetime.now(UTC))


class Ids:
    def execution_id(self) -> str: return "e1"
    def correlation_id(self) -> CorrelationId: return CorrelationId("c")
    def task_id(self) -> str: return "t1"


def _platform(approved: bool, known: tuple[str, ...] = ()) -> EmailPlatform:
    return EmailPlatform(provider=FakeProvider(), notifications=ApproveNotify(approved), # type: ignore
                         ids=Ids(), known_contacts=set(known), # type: ignore
                         approval_channels=("telegram:primary",))


_DRAFT = EmailDraft(to=(EmailAddress(email="boss@corp.com"),),
                    subject="Q3 Report", body_text="Attached.")


@pytest.mark.asyncio
async def test_send_requires_approval_and_previews_real_content() -> None:
    p = _platform(approved=True)
    mid = await p.send(_DRAFT, CorrelationId("c"))
    assert mid == "msg-1"
    # Type hints help tests but provider is a FakeProvider
    prov = p._provider
    assert isinstance(prov, FakeProvider)
    assert prov.sent is _DRAFT                       # only sent after approval
    notify = p._notify
    assert isinstance(notify, ApproveNotify)
    assert notify.previewed is not None
    assert "boss@corp.com" in notify.previewed           # real recipient in preview
    assert "Q3 Report" in notify.previewed               # real subject
    assert "Attached." in notify.previewed               # real body


@pytest.mark.asyncio
async def test_denied_send_never_reaches_provider() -> None:
    p = _platform(approved=False)
    with pytest.raises(CapabilityDenied):
        await p.send(_DRAFT, CorrelationId("c"))
    prov = p._provider
    assert isinstance(prov, FakeProvider)
    assert prov.sent is None                         # nothing sent


@pytest.mark.asyncio
async def test_unknown_contact_flagged_in_preview() -> None:
    p = _platform(approved=True, known=())                  # boss is NOT known
    await p.send(_DRAFT, CorrelationId("c"))
    notify = p._notify
    assert isinstance(notify, ApproveNotify)
    assert notify.previewed is not None
    assert "NEW CONTACT" in notify.previewed


@pytest.mark.asyncio
async def test_known_contact_no_new_warning() -> None:
    p = _platform(approved=True, known=("boss@corp.com",))
    await p.send(_DRAFT, CorrelationId("c"))
    notify = p._notify
    assert isinstance(notify, ApproveNotify)
    assert notify.previewed is not None
    assert "NEW CONTACT" not in notify.previewed
