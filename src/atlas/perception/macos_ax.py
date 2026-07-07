"""macOS accessibility-tree perception backend.

WHY lazy PyObjC imports inside methods: the module must be importable on Linux
CI (where pyobjc is absent). available() gates all real work. WHY we bound the
traversal (max depth/breadth): a full AX tree can be huge; the planner only
needs the interactable surface, and unbounded traversal could hang on a
pathological app.
"""

from __future__ import annotations

from atlas.infra.logging import get_logger
from atlas.infra.platform import has_pyobjc
from atlas.perception.sensitivity import is_sensitive_app
from atlas.perception.types import PerceptionSource, Role, ScreenState, UIElement

_log = get_logger("atlas.perception.ax")

_MAX_DEPTH = 12
_MAX_ELEMENTS = 300

# AXRole string -> our normalized Role. Unknown roles collapse to "other".
_ROLE_MAP: dict[str, Role] = {
    "AXWindow": "window", "AXButton": "button", "AXTextField": "text_field",
    "AXTextArea": "text_field", "AXStaticText": "static_text", "AXMenu": "menu",
    "AXMenuItem": "menu_item", "AXCheckBox": "checkbox", "AXLink": "link",
    "AXImage": "image", "AXGroup": "group", "AXList": "list", "AXRow": "row",
}


class MacOSAXBackend:
    def available(self) -> bool:
        return has_pyobjc()

    def capture_frontmost(self) -> ScreenState:
        if not has_pyobjc():
            return ScreenState(source=PerceptionSource.UNSUPPORTED,
                               note="pyobjc unavailable / not macOS")
        # Lazy imports: only executed on a real Mac with pyobjc present.
        from AppKit import NSWorkspace  # type: ignore[import-untyped]
        from ApplicationServices import (  # type: ignore[import-untyped]
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            kAXErrorSuccess,
        )

        ws = NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        if front is None:
            return ScreenState(source=PerceptionSource.AX_TREE, note="no frontmost app")
        app_name = str(front.localizedName() or "")
        pid = int(front.processIdentifier())

        app_el = AXUIElementCreateApplication(pid)
        err, window = AXUIElementCopyAttributeValue(app_el, "AXFocusedWindow", None)
        elements: list[UIElement] = []
        window_title: str | None = None

        if err == kAXErrorSuccess and window is not None:
            window_title = self._attr_str(window, "AXTitle")
            self._walk(window, elements, depth=0, path="window[0]")
        else:
            _log.info("ax.no_focused_window", event_type="perception", app=app_name)

        state = ScreenState(
            source=PerceptionSource.AX_TREE,
            app_name=app_name,
            window_title=window_title,
            elements=tuple(elements[:_MAX_ELEMENTS]),
            sensitive=is_sensitive_app(app_name),
        )
        _log.info("ax.captured", event_type="perception", app=app_name,
                  elements=len(state.elements), sensitive=state.sensitive)
        return state

    # --- internals ---------------------------------------------------------
    def _walk(self, node: object, out: list[UIElement], *, depth: int, path: str) -> None:
        if depth > _MAX_DEPTH or len(out) >= _MAX_ELEMENTS:
            return
        from ApplicationServices import AXUIElementCopyAttributeValue, kAXErrorSuccess

        role_raw = self._attr_str(node, "AXRole") or ""
        role: Role = _ROLE_MAP.get(role_raw, "other")
        label = self._attr_str(node, "AXTitle") or self._attr_str(node, "AXDescription")
        value = self._attr_str(node, "AXValue")
        enabled = self._attr_bool(node, "AXEnabled", default=True)
        focused = self._attr_bool(node, "AXFocused", default=False)

        # Only record nodes that carry signal (a label/value or an interactable role).
        if label or value or role in ("button", "text_field", "checkbox", "link", "menu_item"):
            leaf_path = f"{path}:{label}" if label else path
            out.append(UIElement(
                role=role, label=label, value=value, enabled=enabled,
                focused=focused, ax_path=leaf_path,
            ))

        err, children = AXUIElementCopyAttributeValue(node, "AXChildren", None)
        if err == kAXErrorSuccess and children:
            for i, child in enumerate(children):
                self._walk(child, out, depth=depth + 1, path=f"{path}/{i}")

    @staticmethod
    def _attr_str(node: object, attr: str) -> str | None:
        from ApplicationServices import AXUIElementCopyAttributeValue, kAXErrorSuccess
        err, val = AXUIElementCopyAttributeValue(node, attr, None)
        if err != kAXErrorSuccess or val is None:
            return None
        text = str(val).strip()
        return text or None

    @staticmethod
    def _attr_bool(node: object, attr: str, *, default: bool) -> bool:
        from ApplicationServices import AXUIElementCopyAttributeValue, kAXErrorSuccess
        err, val = AXUIElementCopyAttributeValue(node, attr, None)
        if err != kAXErrorSuccess or val is None:
            return default
        return bool(val)
