"""Android substrate adapter — one body of ATLAS, built on ADB + uiautomator.

WHY this file exists: the cognitive core speaks PerceptionSnapshot /
ControlAction only. This adapter translates those universal contracts to
``uiautomator dump`` XML (perception) and ``input``/``am``/``monkey`` commands
(control), all behind the injectable AndroidTransport.

Perception: uiautomator gives us a structured accessibility-like dump with
resource-id (stable identity), content-desc/text (labels) and bounds — the
same semantic ingredients as the AX tree and the DOM.

Control: taps are ALWAYS resolved from a fresh dump (locate → center-of-bounds
→ ``input tap``), never from model-supplied bare coordinates. Free-form shell
commands are impossible: every command is rendered from a fixed template with
validated parameters.
"""

from __future__ import annotations

import logging
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime

from atlas.capabilities.computer_use.adapters.android_transport import AndroidTransport
from atlas.control.contracts import ActionCapability, ControlAction, ControlResult
from atlas.perception.contracts import (
    HealthStatus,
    PerceivedElement,
    PerceptionModality,
    PerceptionSnapshot,
    Substrate,
)
from atlas.perception.targets import TargetRef, TargetStrategy

_log = logging.getLogger("atlas.computer_use.android")

_DUMP_CMD = "uiautomator dump /sdcard/atlas_ui.xml >/dev/null 2>&1 && cat /sdcard/atlas_ui.xml"
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9 @._,-]{0,200}$")
_MAX_DUMP_BYTES = 2_000_000  # guard against resource-exhaustion via oversized dumps

_KEYEVENT: dict[str, int] = {"back": 4, "home": 3, "enter": 66, "recent": 187}


@dataclass(frozen=True)
class _AndroidNode:
    label: str | None
    resource_id: str | None
    role: str
    clickable: bool
    enabled: bool
    cx: int
    cy: int


def _parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    m = _BOUNDS_RE.match(raw)
    if not m:
        return None
    x1, y1, x2, y2 = (int(g) for g in m.groups())
    return (x1, y1, x2 - x1, y2 - y1)


def parse_uiautomator_dump(xml_text: str) -> tuple[_AndroidNode, ...]:
    """Parse a uiautomator XML dump into flat nodes. Pure + testable.

    Hardened: oversized input and DOCTYPE/ENTITY declarations are rejected so a
    compromised/malicious dump cannot exhaust memory via entity expansion.
    """
    if len(xml_text) > _MAX_DUMP_BYTES or "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        return ()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ()
    nodes: list[_AndroidNode] = []
    for el in root.iter("node"):
        bounds = _parse_bounds(el.get("bounds", ""))
        if bounds is None:
            continue
        x, y, w, h = bounds
        cls = el.get("class", "")
        nodes.append(
            _AndroidNode(
                label=(el.get("text") or el.get("content-desc") or None),
                resource_id=(el.get("resource-id") or None),
                role=cls.rsplit(".", 1)[-1].lower() if cls else "view",
                clickable=el.get("clickable") == "true",
                enabled=el.get("enabled") == "true",
                cx=x + w // 2,
                cy=y + h // 2,
            )
        )
    return tuple(nodes)


def _find_node(nodes: tuple[_AndroidNode, ...], target: TargetRef) -> _AndroidNode | None:
    wanted = target.value
    if target.strategy is TargetStrategy.RESOURCE_ID:
        return next((n for n in nodes if n.resource_id == wanted), None)
    wanted_text = target.text or target.label or target.value
    exact = target.exact
    matches = [
        n for n in nodes if n.label and (n.label == wanted_text if exact else wanted_text.lower() in n.label.lower())
    ]
    if not matches:
        return None
    return matches[target.nth] if target.nth is not None and target.nth < len(matches) else matches[0]


# ---------------------------------------------------------------------------
# Perception
# ---------------------------------------------------------------------------


class AndroidPerceptionAdapter:
    """PerceptionAdapter over a uiautomator dump."""

    substrate = Substrate.ANDROID

    def __init__(self, transport: AndroidTransport) -> None:
        self._transport = transport

    async def snapshot(self, target: object | None = None) -> PerceptionSnapshot:
        result = await self._transport.shell(_DUMP_CMD, timeout_s=20.0)
        if not result.ok or "<hierarchy" not in result.stdout:
            return PerceptionSnapshot(
                id=uuid.uuid4().hex,
                substrate=Substrate.ANDROID,
                source="uiautomator",
                captured_ts=datetime.now(UTC),
                confidence=0.0,
                note=f"dump failed: {result.stderr or result.stdout or 'empty'}",
            )
        nodes = parse_uiautomator_dump(result.stdout)
        elements = tuple(
            PerceivedElement(
                role=n.role,
                label=n.label,
                enabled=n.enabled,
                stable_id=n.resource_id,
                confidence=0.9,
                metadata={"clickable": str(n.clickable).lower()},
            )
            for n in nodes
        )
        return PerceptionSnapshot(
            id=uuid.uuid4().hex,
            substrate=Substrate.ANDROID,
            source="uiautomator",
            captured_ts=datetime.now(UTC),
            modalities=(PerceptionModality.STRUCTURE, PerceptionModality.TEXT, PerceptionModality.APP_STATE),
            elements=elements,
            confidence=0.85,
        )

    async def health(self) -> HealthStatus:
        if await self._transport.is_connected():
            return HealthStatus(available=True, detail="Android device connected")
        return HealthStatus(available=False, detail="no Android device detected")

    async def capabilities(self) -> tuple[PerceptionModality, ...]:
        return (PerceptionModality.STRUCTURE, PerceptionModality.TEXT, PerceptionModality.APP_STATE)


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------


class AndroidControlAdapter:
    """ControlAdapter over ``input``/``am``/``monkey`` via the transport."""

    substrate = Substrate.ANDROID

    def __init__(self, transport: AndroidTransport) -> None:
        self._transport = transport

    async def health(self) -> HealthStatus:
        if await self._transport.is_connected():
            return HealthStatus(available=True, detail="Android device connected")
        return HealthStatus(available=False, detail="no Android device detected")

    async def capabilities(self) -> tuple[str, ...]:
        return ("launch", "tap", "long_press", "swipe", "type_text", "press_key")

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def dispatch(self, action: ControlAction) -> ControlResult:
        if action.capability is ActionCapability.OBSERVATION:
            return ControlResult(ok=False, error="observation is handled by the perception adapter")
        op = action.operation
        try:
            if op == "launch":
                package = str(action.arguments.get("package", ""))
                if not re.fullmatch(r"[A-Za-z0-9_.]+", package):
                    return ControlResult(ok=False, error="launch requires a valid package name")
                return await self._run(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
            if op in {"tap", "long_press"}:
                if action.target is None:
                    return ControlResult(ok=False, error=f"{op} requires a target")
                node = await self._locate(action.target)
                if node is None:
                    return ControlResult(ok=False, error="target not found in current UI dump")
                if op == "tap":
                    return await self._run(f"input tap {node.cx} {node.cy}")
                return await self._run(f"input swipe {node.cx} {node.cy} {node.cx} {node.cy} 800")
            if op == "swipe":
                direction = str(action.arguments.get("direction", "up"))
                vectors = {
                    "up": "500 1500 500 500",
                    "down": "500 500 500 1500",
                    "left": "900 1000 100 1000",
                    "right": "100 1000 900 1000",
                }
                if direction not in vectors:
                    return ControlResult(ok=False, error=f"swipe direction must be one of {sorted(vectors)}")
                return await self._run(f"input swipe {vectors[direction]} 200")
            if op == "type_text":
                text = str(action.arguments.get("text", ""))
                if not _SAFE_TEXT_RE.match(text):
                    return ControlResult(ok=False, error="type_text supports plain ASCII text up to 200 chars")
                return await self._run(f"input text '{text.replace(' ', '%s')}'")
            if op == "press_key":
                key = str(action.arguments.get("key", "")).lower()
                code = _KEYEVENT.get(key)
                if code is None:
                    return ControlResult(ok=False, error=f"press_key supports only {sorted(_KEYEVENT)}")
                return await self._run(f"input keyevent {code}")
            return ControlResult(ok=False, error=f"unknown operation: {op}")
        except Exception as exc:
            return ControlResult(ok=False, error=str(exc))

    async def _locate(self, target: TargetRef) -> _AndroidNode | None:
        result = await self._transport.shell(_DUMP_CMD, timeout_s=20.0)
        if not result.ok:
            return None
        return _find_node(parse_uiautomator_dump(result.stdout), target)

    async def _run(self, command: str) -> ControlResult:
        result = await self._transport.shell(command)
        return ControlResult(
            ok=result.ok,
            output=result.stdout or None,
            error=result.stderr or None if not result.ok else None,
            evidence=f"adb: {command.split()[0]} {command.split()[1] if len(command.split()) > 1 else ''}",
        )
