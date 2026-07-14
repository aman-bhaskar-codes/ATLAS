import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from atlas.capabilities.browser.engines.submit import SubmitEngine
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.domain.content import FormModel, FormField
from atlas.infra.ids import CorrelationId, UuidGenerator
from atlas.capabilities.errors import CapabilityDenied
from atlas.capabilities.notification.domain.models import ApprovalDecision

@pytest.mark.asyncio
async def test_submit_engine_approval():
    dispatcher = AsyncMock()
    notifications = AsyncMock()
    notifications.request_approval.return_value = ApprovalDecision(
        request_id="req_123", approved=True, decided_ts=datetime.now(timezone.utc)
    )
    ids = UuidGenerator()
    state_builder = AsyncMock()
    state_builder.build_state.return_value = None
    
    engine = SubmitEngine(
        dispatcher=dispatcher,
        notifications=notifications,
        ids=ids,
        approval_channels=("test",),
        state_builder=state_builder
    )
    
    handle = PageHandle(session_id="test", tab_id="test")
    form = FormModel(id="login", action_url="https://example.com/login", fields=(
        FormField(name="username"),
        FormField(name="password", kind="password")
    ))
    values = {"username": "user", "password": "secret_password"}
    
    result = await engine.submit(handle, form, values, CorrelationId("cid_123"))
    
    assert result.ok
    assert result.action.kind == "submit"
    
    # Check that approval was requested
    notifications.request_approval.assert_called_once()
    req = notifications.request_approval.call_args[0][0]
    
    # Check password redaction
    assert "secret_password" not in req.detail
    assert "••••" in req.detail

@pytest.mark.asyncio
async def test_submit_engine_denial():
    dispatcher = AsyncMock()
    notifications = AsyncMock()
    notifications.request_approval.return_value = ApprovalDecision(
        request_id="req_456", approved=False, decided_ts=datetime.now(timezone.utc)
    )
    ids = UuidGenerator()
    state_builder = AsyncMock()
    
    engine = SubmitEngine(
        dispatcher=dispatcher,
        notifications=notifications,
        ids=ids,
        approval_channels=("test",),
        state_builder=state_builder
    )
    
    handle = PageHandle(session_id="test", tab_id="test")
    form = FormModel(id="login", action_url="https://example.com/login", fields=())
    values = {}
    
    with pytest.raises(CapabilityDenied, match="form submit not approved"):
        await engine.submit(handle, form, values, CorrelationId("cid_123"))
