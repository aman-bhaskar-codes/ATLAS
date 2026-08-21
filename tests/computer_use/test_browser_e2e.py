"""Browser E2E — real chromium + local HTML fixture through universal contracts.

WHY: Phase 52 requires proof that the ONE perceive → resolve → act →
re-perceive → verify loop works on a REAL substrate. This test drives the full
ComputerUseEngine against a live headless chromium over a file:// fixture:

    navigate → perceive (DOM + accessibility) → type (role target) →
    click (stable-id target) → verify text evidence

No mocks on the browser path. Phase 47 honesty is asserted too: a target that
does not exist must be an honest failure, never a claimed click.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from playwright.async_api import Browser, Page, async_playwright
from playwright.async_api import Locator as PWLocator

from atlas.capabilities.browser.domain.dom import AccessibilityNode, BoundingBox, ElementRef
from atlas.capabilities.browser.domain.locator import Locator, LocatorKind
from atlas.capabilities.browser.domain.page import PageHandle, PageState
from atlas.capabilities.browser.domain.vision import Screenshot
from atlas.capabilities.browser.platform import BrowserPlatform
from atlas.capabilities.computer_use.adapters.browser import (
    BrowserContext,
    BrowserControlAdapter,
    BrowserPerceptionAdapter,
)
from atlas.capabilities.computer_use.engine import ComputerUseEngine
from atlas.capabilities.computer_use.verify import ExpectationKind, ExpectationSpec
from atlas.control.contracts import ActionCapability, ControlAction
from atlas.infra.ids import CorrelationId
from atlas.perception.contracts import Substrate
from atlas.perception.targets import TargetRef, TargetStrategy

_FIXTURE_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>ATLAS Fixture</title></head>
<body>
  <h1>Greeting Form</h1>
  <label for="name-input">Your name</label>
  <input id="name-input" type="text" />
  <button id="submit-btn">Submit</button>
  <p id="result" aria-live="polite"></p>
  <script>
    document.getElementById("submit-btn").addEventListener("click", () => {
      const name = document.getElementById("name-input").value;
      document.getElementById("result").textContent = "Hello, " + name + "!";
    });
  </script>
</body>
</html>
"""


_NODE_RE = re.compile(r'^- ([\w]+)(?: "(.*)")?(?: \[(.*)\])?$')
_ATTR_RE = re.compile(r'(\w+)=([^ \]]+)')


def _parse_aria_snapshot(text: str) -> AccessibilityNode | None:
    """Parse playwright's ``aria_snapshot()`` YAML into AccessibilityNodes.

    Replaces the removed ``page.accessibility`` API (playwright >= 1.40).
    """
    try:
        items = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(items, list) or not items:
        return None

    def build(item: Any) -> AccessibilityNode:
        role, name, attrs, children, scalar = "generic", "", {}, [], None
        if isinstance(item, str):
            m = _NODE_RE.match(item)
            if m:
                role = m.group(1)
                name = m.group(2) or ""
                attrs = dict(_ATTR_RE.findall(m.group(3) or ""))
            else:
                role, name = "text", item
        elif isinstance(item, dict):
            for key, value in item.items():
                m = _NODE_RE.match(f"- {key}")
                if m:
                    role = m.group(1)
                    name = m.group(2) or ""
                    attrs = dict(_ATTR_RE.findall(m.group(3) or ""))
                if isinstance(value, list):
                    children.extend(build(child) for child in value)
                elif value is not None:
                    scalar = str(value)
        value = attrs.get("value", "")
        if scalar is not None and role in {"textbox", "combobox", "searchbox", "slider", "spinbutton"}:
            value = scalar  # "- textbox \"x\": ATLAS" — the scalar is the input value
        elif scalar is not None:
            children.append(AccessibilityNode(role="text", name=scalar))
        return AccessibilityNode(
            role=role,
            name=name,
            value=str(value),
            disabled=attrs.get("disabled") == "true",
            focused=attrs.get("focused") == "true",
            children=tuple(children),
        )

    roots = tuple(build(item) for item in items)
    return AccessibilityNode(role="WebArea", children=roots)


@dataclass
class _Session:
    id: str


class MiniBrowserPlatform:
    """Minimal REAL platform satisfying the browser adapter's method surface.

    Deliberately bypasses ``build_browser_platform`` (notifications, URL
    reputation) so a local file:// fixture can be driven deterministically.
    Everything underneath is genuine playwright chromium.
    """

    def __init__(self, browser: Browser) -> None:
        self._browser = browser
        self._pages: dict[str, Page] = {}  # tab_id → live page
        self._session_pages: dict[str, list[str]] = {}

    async def create_session(self) -> _Session:
        session = _Session(id=f"sess-{len(self._session_pages) + 1}")
        self._session_pages[session.id] = []
        return session

    async def new_page(self, session_id: str) -> PageHandle:
        page = await self._browser.new_page()
        tab_id = f"tab-{len(self._pages) + 1}"
        self._pages[tab_id] = page
        self._session_pages.setdefault(session_id, []).append(tab_id)
        return PageHandle(session_id=session_id, tab_id=tab_id)

    async def close_session(self, session_id: str) -> None:
        for tab_id in self._session_pages.pop(session_id, []):
            page = self._pages.pop(tab_id, None)
            if page is not None and not page.is_closed():
                await page.close()

    # --- state ─────────────────────────────────────────────────────── #

    async def build_state(self, handle: PageHandle) -> PageState:
        page = self._pages[handle.tab_id]
        ax_root = _parse_aria_snapshot(await page.aria_snapshot())
        accessibility = (ax_root,) if ax_root else ()

        visible: list[ElementRef] = []
        loc = page.locator("button, input, textarea, select, a[href]")
        for i in range(await loc.count()):
            el = loc.nth(i)
            text = ""
            try:
                text = await el.inner_text()
            except Exception:
                pass  # inputs have no inner text
            attrs: dict[str, str] = {}
            for attr in ("href", "type", "name", "aria-label"):
                value = await el.get_attribute(attr)
                if value:
                    attrs[attr] = value
            box = await el.bounding_box()
            visible.append(
                ElementRef(
                    id=await el.get_attribute("id") or "",
                    tag_name=str(await el.evaluate("e => e.tagName.toLowerCase()")),
                    text=text,
                    attributes=attrs,
                    bounding_box=(
                        BoundingBox(x=box["x"], y=box["y"], width=box["width"], height=box["height"])
                        if box
                        else None
                    ),
                )
            )
        dom_hash = hashlib.sha256(
            str(await page.evaluate("document.body.innerHTML")).encode()
        ).hexdigest()[:16]
        return PageState(
            handle=handle,
            url=page.url,
            title=await page.title(),
            visible_elements=tuple(visible),
            accessibility=accessibility,
            captured_ts=datetime.now(UTC),
            dom_hash=dom_hash,
        )

    async def capture_screenshot(self, handle: PageHandle, full_page: bool, cid: CorrelationId) -> Screenshot:
        page = self._pages[handle.tab_id]
        return Screenshot(data=await page.screenshot(full_page=full_page), mime_type="image/png")

    # --- navigation ────────────────────────────────────────────────── #

    async def goto(self, handle: PageHandle, url: str, cid: CorrelationId) -> PageState:
        await self._pages[handle.tab_id].goto(url, wait_until="load")
        return await self.build_state(handle)

    async def back(self, handle: PageHandle, cid: CorrelationId) -> PageState:
        await self._pages[handle.tab_id].go_back()
        return await self.build_state(handle)

    async def forward(self, handle: PageHandle, cid: CorrelationId) -> PageState:
        await self._pages[handle.tab_id].go_forward()
        return await self.build_state(handle)

    async def reload(self, handle: PageHandle, cid: CorrelationId) -> PageState:
        await self._pages[handle.tab_id].reload()
        return await self.build_state(handle)

    # --- interaction ───────────────────────────────────────────────── #

    def _resolve(self, page: Page, locator: Locator) -> PWLocator:
        if locator.kind is LocatorKind.CSS:
            resolved = page.locator(locator.value)
        elif locator.kind is LocatorKind.XPATH:
            resolved = page.locator(f"xpath={locator.value}")
        elif locator.kind is LocatorKind.TEXT:
            resolved = page.get_by_text(locator.value, exact=locator.exact)
        elif locator.kind is LocatorKind.ROLE:
            resolved = page.get_by_role(locator.value, name=locator.name)
        else:
            resolved = page.get_by_label(locator.value, exact=locator.exact)
        return resolved.nth(locator.nth) if locator.nth is not None else resolved

    async def click(self, handle: PageHandle, locator: Locator, cid: CorrelationId) -> None:
        await self._resolve(self._pages[handle.tab_id], locator).first.click()

    async def type_text(self, handle: PageHandle, locator: Locator, text: str, cid: CorrelationId) -> None:
        await self._resolve(self._pages[handle.tab_id], locator).first.fill(text)


# ---------------------------------------------------------------------------
# Fixture: engine wired to a live chromium over the local HTML fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def live_engine(tmp_path: Path):
    fixture = tmp_path / "fixture.html"
    fixture.write_text(_FIXTURE_HTML, encoding="utf-8")
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(headless=True)
    except Exception as exc:  # no chromium on this machine → honest skip
        await pw.stop()
        pytest.skip(f"chromium unavailable: {exc}")
    platform = MiniBrowserPlatform(browser)
    ctx = BrowserContext(cast(BrowserPlatform, platform))
    perception = BrowserPerceptionAdapter(ctx)
    engine = ComputerUseEngine(
        {Substrate.BROWSER: perception},
        {Substrate.BROWSER: BrowserControlAdapter(ctx)},
    )
    try:
        yield engine, perception, fixture
    finally:
        await engine.shutdown()
        await browser.close()
        await pw.stop()


async def _navigate(engine: ComputerUseEngine, fixture: Path) -> None:
    outcome = await engine.act(
        Substrate.BROWSER,
        ControlAction(
            capability=ActionCapability.NAVIGATION,
            operation="navigate",
            arguments={"url": fixture.as_uri()},
        ),
        expectations=(ExpectationSpec(kind=ExpectationKind.URL_CONTAINS, value="fixture.html"),),
    )
    assert outcome.ok, outcome.note or outcome.result
    assert outcome.verification is not None and outcome.verification.verified


async def test_browser_e2e_type_click_verify(live_engine) -> None:
    engine, perception, fixture = live_engine
    await _navigate(engine, fixture)

    # 1. perception sees the real page through universal contracts
    snap = await engine.perceive(Substrate.BROWSER)
    labels = {el.label for el in snap.elements if el.label}
    assert "Submit" in labels
    assert snap.substrate is Substrate.BROWSER

    # 2. type via role target (resolved from the accessibility tree)
    typed = await engine.act(
        Substrate.BROWSER,
        ControlAction(
            capability=ActionCapability.UI,
            operation="type_text",
            target=TargetRef(strategy=TargetStrategy.ROLE, value="textbox", text="Your name"),
            arguments={"text": "ATLAS"},
        ),
    )
    assert typed.ok, typed.note
    assert typed.resolved is not None  # resolved from evidence (role or text), never guessed
    assert typed.after is not None
    # the real proof: the typed value is visible in re-perception
    assert "ATLAS" in {el.value for el in typed.after.elements if el.role == "textbox"}

    # 3. click via stable-id target; the typed text must surface as evidence
    clicked = await engine.act(
        Substrate.BROWSER,
        ControlAction(
            capability=ActionCapability.UI,
            operation="click",
            target=TargetRef(strategy=TargetStrategy.STABLE_ID, value="submit-btn"),
        ),
        expectations=(ExpectationSpec(kind=ExpectationKind.TEXT_PRESENT, value="Hello, ATLAS!"),),
    )
    assert clicked.ok, clicked.note
    assert clicked.resolved is not None and clicked.resolved.strategy_used is TargetStrategy.STABLE_ID
    assert clicked.verification is not None and clicked.verification.verified

    # 4. visual evidence is a real PNG, on demand only
    shot = await perception.visual_evidence()
    assert shot is not None and shot.data[:8] == b"\x89PNG\r\n\x1a\n"


async def test_missing_target_is_an_honest_failure(live_engine) -> None:
    engine, _, fixture = live_engine
    await _navigate(engine, fixture)
    outcome = await engine.act(
        Substrate.BROWSER,
        ControlAction(
            capability=ActionCapability.UI,
            operation="click",
            target=TargetRef(strategy=TargetStrategy.TEXT, value="No Such Button"),
        ),
    )
    assert not outcome.ok
    assert outcome.note is not None and "target not found in perception" in outcome.note
    assert outcome.verification is None  # never claim verification without evidence
    assert outcome.result is None  # nothing was dispatched — no fake attempt
