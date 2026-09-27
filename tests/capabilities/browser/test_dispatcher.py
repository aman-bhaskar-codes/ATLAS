from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from atlas.capabilities.browser.dispatcher import (
    NullBrowserDispatcher,
    PlaywrightBrowserDispatcher,
)
from atlas.capabilities.browser.domain.action import ActionKind, BrowserAction
from atlas.capabilities.browser.domain.content import FormField, FormModel
from atlas.capabilities.browser.domain.locator import Locator
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.engines.submit import SubmitEngine
from atlas.capabilities.errors import CapabilityDenied, NoProviderAvailable, ProviderExecutionError
from atlas.capabilities.notification.domain.models import ApprovalDecision
from atlas.infra.ids import CorrelationId, UuidGenerator
from atlas.infra.types import ToolResult, ToolRequest
from atlas.safety.engine import HaltedError, DeniedError

@pytest.mark.asyncio
async def test_null_dispatcher_raises():
    dispatcher = NullBrowserDispatcher()
    handle = PageHandle(session_id="test", tab_id="test")
    action = BrowserAction(handle=handle, kind=ActionKind.CLICK, locator=Locator(kind="css", value="btn"))
    
    with pytest.raises(NoProviderAvailable, match="browser execution not wired"):
        await dispatcher.dispatch(action, CorrelationId("cid_123"))

@pytest.mark.asyncio
async def test_submit_engine_null_dispatcher_fails_early():
    # Submit engine must raise NoProviderAvailable before requesting approval
    notifications = AsyncMock()
    engine = SubmitEngine(
        dispatcher=NullBrowserDispatcher(),
        notifications=notifications,
        ids=UuidGenerator(),
        approval_channels=("test",),
        state_builder=AsyncMock(),
    )
    handle = PageHandle(session_id="test", tab_id="test")
    form = FormModel(id="test", action_url="https://example.com/login", fields=())
    
    with pytest.raises(NoProviderAvailable, match="browser execution not wired"):
        await engine.submit(handle, form, {}, CorrelationId("cid"))
        
    notifications.request_approval.assert_not_called()

@pytest.mark.asyncio
async def test_playwright_dispatcher_approval_granted():
    provider = AsyncMock()
    safety = AsyncMock()
    # Safety engine guard resolves successfully
    safety.guard.return_value = ToolResult(ok=True)
    
    dispatcher = PlaywrightBrowserDispatcher(provider=provider, safety=safety)
    handle = PageHandle(session_id="sess_1", tab_id="tab_1")
    action = BrowserAction(handle=handle, kind=ActionKind.CLICK, locator=Locator(kind="css", value="#btn"))
    cid = CorrelationId("cid_456")
    
    await dispatcher.dispatch(action, cid)
    
    # Safety guard called
    safety.guard.assert_called_once()
    req = safety.guard.call_args[0][0]
    tool = safety.guard.call_args[0][1]
    
    assert req.tool == "browser"
    assert req.operation == "click"
    
    # Call the tool's execute directly to ensure it wraps the provider
    await tool.execute({})
    provider.click.assert_called_once_with("sess_1", "tab_1", action.locator)

@pytest.mark.asyncio
async def test_playwright_dispatcher_denial():
    provider = AsyncMock()
    safety = AsyncMock()
    
    # Simulate safety blocking the action
    safety.guard.side_effect = DeniedError(decision=AsyncMock())
    
    dispatcher = PlaywrightBrowserDispatcher(provider=provider, safety=safety)
    handle = PageHandle(session_id="sess_1", tab_id="tab_1")
    action = BrowserAction(handle=handle, kind=ActionKind.CLICK, locator=Locator(kind="css", value="#btn"))
    
    with pytest.raises(DeniedError):
        await dispatcher.dispatch(action, CorrelationId("cid"))
        
    # Provider is never called
    provider.click.assert_not_called()

@pytest.mark.asyncio
async def test_playwright_dispatcher_provider_error():
    provider = AsyncMock()
    safety = AsyncMock()
    
    # Tool executes normally in the guard, but the provider raises
    async def fake_guard(req, tool):
        return await tool.execute({})
    safety.guard.side_effect = fake_guard
    
    # Provider raises an exception
    provider.click.side_effect = Exception("detached handle")
    
    dispatcher = PlaywrightBrowserDispatcher(provider=provider, safety=safety)
    handle = PageHandle(session_id="sess_1", tab_id="tab_1")
    action = BrowserAction(handle=handle, kind=ActionKind.CLICK, locator=Locator(kind="css", value="#btn"))
    
    with pytest.raises(ProviderExecutionError, match="Playwright error: detached handle"):
        await dispatcher.dispatch(action, CorrelationId("cid"))

import pathlib
import tempfile

from atlas.capabilities.browser.providers.playwright_provider import PlaywrightProvider

@pytest.mark.asyncio
async def test_playwright_dispatcher_happy_path(require_browser: None):
    # This test spins up a real browser to ensure the dispatcher integration actually works
    provider = PlaywrightProvider()
    safety = AsyncMock()
    # Mock safety to just execute the tool (i.e. approve)
    async def fake_guard(req, tool):
        return await tool.execute({})
    safety.guard.side_effect = fake_guard
    
    dispatcher = PlaywrightBrowserDispatcher(provider=provider, safety=safety)
    
    # Create temp HTML
    html = """
    <html><body>
        <input type="text" id="myinput" value="" />
        <button id="mybtn" onclick="document.getElementById('myinput').value='clicked'">Click Me</button>
        <form id="myform" onsubmit="event.preventDefault(); document.getElementById('myinput').value='submitted';">
            <button type="submit" id="mysubmit">Submit Form</button>
        </form>
    </body></html>
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
        f.write(html)
        tmp_path = f.name
        
    try:
        session_id = await provider.launch(profile=None, incognito=True, sandbox_spec=None)
        tab_id = await provider.new_tab(session_id)
        await provider.goto(session_id, tab_id, f"file://{tmp_path}")
        
        handle = PageHandle(session_id=session_id, tab_id=tab_id)
        cid = CorrelationId("cid_happy")
        
        # 1. Test click
        action_click = BrowserAction(handle=handle, kind=ActionKind.CLICK, locator=Locator(kind="css", value="#mybtn"))
        await dispatcher.dispatch(action_click, cid)
        val = await provider.eval_readonly(session_id, tab_id, "document.getElementById('myinput').value")
        assert val == "clicked"
        
        # 2. Test type
        action_type = BrowserAction(handle=handle, kind=ActionKind.TYPE, locator=Locator(kind="css", value="#myinput"), value="typed_text")
        await dispatcher.dispatch(action_type, cid)
        val2 = await provider.eval_readonly(session_id, tab_id, "document.getElementById('myinput').value")
        assert val2 == "typed_text"
        
        # 3. Test submit
        action_submit = BrowserAction(handle=handle, kind=ActionKind.SUBMIT, locator=Locator(kind="css", value="#mysubmit"))
        await dispatcher.dispatch(action_submit, cid)
        val3 = await provider.eval_readonly(session_id, tab_id, "document.getElementById('myinput').value")
        assert val3 == "submitted"
        
    finally:
        await provider.stop()
        pathlib.Path(tmp_path).unlink(missing_ok=True)
