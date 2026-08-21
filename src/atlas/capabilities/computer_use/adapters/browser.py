"""Browser substrate adapter — one body of ATLAS, built on BrowserPlatform.

WHY this file exists: the cognitive core speaks PerceptionSnapshot /
ControlAction only. This adapter translates those universal contracts to the
existing Playwright-backed BrowserPlatform (sessions, pages, engines).

Design notes:
* Both the perception and the control adapter share ONE ``BrowserContext`` so
  that "perceive" and "act" observe/mutate the SAME page.
* PageState → PerceptionSnapshot conversion flattens the accessibility slice
  and the visible-element list into PerceivedElements; the raw DOM never leaks
  upward.
* TargetRef → Locator translation is the ONLY place substrate addressing
  happens. Coordinates are rejected here (the browser never needs pixel taps).
* The correlation id travels via the reserved ``arguments["correlation_id"]``
  key so every engine call stays auditable.
"""

from __future__ import annotations

import logging
import uuid

from atlas.capabilities.browser.domain.dom import AccessibilityNode
from atlas.capabilities.browser.domain.locator import Locator, LocatorKind
from atlas.capabilities.browser.domain.page import PageHandle
from atlas.capabilities.browser.platform import BrowserPlatform
from atlas.capabilities.computer_use.adapters._cid import correlation_id_of
from atlas.control.contracts import ActionCapability, ControlAction, ControlResult
from atlas.perception.contracts import (
    HealthStatus,
    PerceivedElement,
    PerceptionModality,
    PerceptionSnapshot,
    Substrate,
    VisualEvidence,
)
from atlas.perception.targets import TargetRef, TargetStrategy

_log = logging.getLogger("atlas.computer_use.browser")


class BrowserContext:
    """Shared session/page owner for the browser perception + control adapters."""

    def __init__(self, platform: BrowserPlatform) -> None:
        self._platform = platform
        self._session_id: str | None = None
        self._handle: PageHandle | None = None

    @property
    def platform(self) -> BrowserPlatform:
        return self._platform

    async def ensure_page(self) -> PageHandle:
        if self._handle is not None:
            return self._handle
        session = await self._platform.create_session()
        self._session_id = session.id
        self._handle = await self._platform.new_page(session.id)
        return self._handle

    async def current_page(self) -> PageHandle | None:
        return self._handle

    async def close(self) -> None:
        if self._session_id is not None:
            await self._platform.close_session(self._session_id)
        self._session_id = None
        self._handle = None


# ---------------------------------------------------------------------------
# Perception
# ---------------------------------------------------------------------------


def _flatten_ax(nodes: tuple[AccessibilityNode, ...], limit: int = 200) -> list[PerceivedElement]:
    out: list[PerceivedElement] = []

    def walk(node: AccessibilityNode) -> None:
        if len(out) >= limit:
            return
        if node.role and (node.name or node.role not in {"none", "generic"}):
            out.append(
                PerceivedElement(
                    role=node.role,
                    label=node.name or None,
                    value=str(node.value) if node.value not in ("", None) else None,
                    enabled=not node.disabled,
                    focused=node.focused,
                    confidence=0.95,
                )
            )
        for child in node.children:
            walk(child)

    for node in nodes:
        walk(node)
    return out


class BrowserPerceptionAdapter:
    """PerceptionAdapter over a live browser page."""

    substrate = Substrate.BROWSER

    def __init__(self, context: BrowserContext) -> None:
        self._ctx = context

    async def snapshot(self, target: object | None = None) -> PerceptionSnapshot:
        handle = await self._ctx.ensure_page()
        state = await self._ctx.platform.build_state(handle)
        elements = _flatten_ax(state.accessibility)
        for el in state.visible_elements:
            elements.append(
                PerceivedElement(
                    role=el.tag_name or "element",
                    label=(el.text.strip()[:80] or None),
                    stable_id=el.id,
                    bounds=(
                        (
                            int(el.bounding_box.x),
                            int(el.bounding_box.y),
                            int(el.bounding_box.width),
                            int(el.bounding_box.height),
                        )
                        if el.bounding_box
                        else None
                    ),
                    confidence=0.9,
                    metadata={k: v for k, v in el.attributes.items() if k in {"href", "type", "name", "aria-label"}},
                )
            )
        return PerceptionSnapshot(
            id=uuid.uuid4().hex,
            substrate=Substrate.BROWSER,
            source="dom+accessibility",
            captured_ts=state.captured_ts,
            url=state.url,
            window_title=state.title or None,
            modalities=(
                PerceptionModality.DOM,
                PerceptionModality.ACCESSIBILITY,
                PerceptionModality.STRUCTURE,
                PerceptionModality.TEXT,
            ),
            elements=tuple(elements),
            state={"loading": state.loading, "dom_hash": state.dom_hash, "auth": state.auth.value},
            confidence=0.92 if not state.loading else 0.5,
        )

    async def visual_evidence(self, *, full_page: bool = False) -> VisualEvidence | None:
        """On-demand visual modality (screenshot). Kept out of snapshot() so
        perception stays cheap unless deep evidence is explicitly requested."""
        handle = await self._ctx.current_page()
        if handle is None:
            return None
        cid = correlation_id_of({})
        shot = await self._ctx.platform.capture_screenshot(handle, full_page, cid)
        fmt = "png" if "png" in shot.mime_type else "jpeg"
        return VisualEvidence(format=fmt, data=shot.data, redacted=False)

    async def health(self) -> HealthStatus:
        try:
            await self._ctx.ensure_page()
        except Exception as exc:
            return HealthStatus(available=False, detail=f"browser unavailable: {exc}")
        return HealthStatus(available=True, detail="browser page ready")

    async def capabilities(self) -> tuple[PerceptionModality, ...]:
        return (
            PerceptionModality.DOM,
            PerceptionModality.ACCESSIBILITY,
            PerceptionModality.STRUCTURE,
            PerceptionModality.TEXT,
            PerceptionModality.VISION,
        )


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------


def target_to_locator(target: TargetRef) -> Locator:
    """Translate universal TargetRef into the browser's provider-neutral Locator."""
    s = target.strategy
    if s is TargetStrategy.DOM_SELECTOR:
        return Locator(kind=LocatorKind.CSS, value=target.value, nth=target.nth)
    if s is TargetStrategy.XPATH:
        return Locator(kind=LocatorKind.XPATH, value=target.value, nth=target.nth)
    if s is TargetStrategy.STABLE_ID:
        return Locator(kind=LocatorKind.CSS, value=f'[id="{target.value}"]')
    if s is TargetStrategy.TEXT or s is TargetStrategy.SEMANTIC:
        return Locator(kind=LocatorKind.TEXT, value=target.text or target.value, exact=target.exact, nth=target.nth)
    if s in (TargetStrategy.ROLE, TargetStrategy.ACCESSIBILITY_ID):
        return Locator(kind=LocatorKind.ROLE, value=target.value, name=target.text or target.label, nth=target.nth)
    if s is TargetStrategy.IMAGE_REF or s is TargetStrategy.COORDINATES:
        raise ValueError("visual/coordinate targets are not supported by the browser adapter")
    return Locator(kind=LocatorKind.LABEL, value=target.label or target.value, nth=target.nth)


class BrowserControlAdapter:
    """ControlAdapter over BrowserPlatform navigation/click/type engines."""

    substrate = Substrate.BROWSER

    def __init__(self, context: BrowserContext) -> None:
        self._ctx = context

    async def health(self) -> HealthStatus:
        try:
            await self._ctx.ensure_page()
        except Exception as exc:
            return HealthStatus(available=False, detail=f"browser unavailable: {exc}")
        return HealthStatus(available=True, detail="browser control ready")

    async def capabilities(self) -> tuple[str, ...]:
        return ("navigate", "back", "forward", "reload", "click", "type_text")

    async def start(self) -> None:
        await self._ctx.ensure_page()

    async def stop(self) -> None:
        await self._ctx.close()

    async def dispatch(self, action: ControlAction) -> ControlResult:
        if action.capability is ActionCapability.OBSERVATION:
            return ControlResult(ok=False, error="observation is handled by the perception adapter")
        platform = self._ctx.platform
        cid = correlation_id_of(action.arguments)
        try:
            handle = await self._ctx.ensure_page()
            op = action.operation
            if op in {"navigate", "goto"}:
                url = str(action.arguments.get("url", ""))
                if not url:
                    return ControlResult(ok=False, error="navigate requires arguments.url")
                state = await platform.goto(handle, url, cid)
                return ControlResult(ok=True, evidence=f"url={state.url} title={state.title!r}")
            if op in {"back", "forward", "reload"}:
                fn = {"back": platform.back, "forward": platform.forward, "reload": platform.reload}[op]
                state = await fn(handle, cid)
                return ControlResult(ok=True, evidence=f"url={state.url} title={state.title!r}")
            if op in {"click", "type_text"}:
                if action.target is None:
                    return ControlResult(ok=False, error=f"{op} requires a target")
                try:
                    locator = target_to_locator(action.target)
                except ValueError as exc:
                    return ControlResult(ok=False, error=str(exc))
                if op == "click":
                    await platform.click(handle, locator, cid)
                else:
                    text = str(action.arguments.get("text", ""))
                    if not text:
                        return ControlResult(ok=False, error="type_text requires arguments.text")
                    await platform.type_text(handle, locator, text, cid)
                state = await platform.build_state(handle)
                evidence = f"url={state.url} title={state.title!r} dom_hash={state.dom_hash}"
                return ControlResult(ok=True, evidence=evidence)
            return ControlResult(ok=False, error=f"unknown operation: {op}")
        except Exception as exc:
            return ControlResult(ok=False, error=str(exc))
