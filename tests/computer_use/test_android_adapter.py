"""Android adapter tests — deterministic, fake-transport (no device needed)."""

from __future__ import annotations

from atlas.capabilities.computer_use.adapters.android import (
    AndroidControlAdapter,
    AndroidPerceptionAdapter,
    parse_uiautomator_dump,
)
from atlas.capabilities.computer_use.adapters.android_transport import TransportResult
from atlas.control.contracts import ActionCapability, ControlAction
from atlas.perception.targets import TargetRef, TargetStrategy

_DUMP = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" clickable="false" enabled="true">
    <node class="android.widget.Button" text="Settings" resource-id="com.app:id/settings_btn"
          bounds="[100,200][300,280]" clickable="true" enabled="true"/>
    <node class="android.widget.TextView" content-desc="Profile"
          bounds="[400,200][600,280]" clickable="true" enabled="true"/>
  </node>
</hierarchy>"""


class FakeTransport:
    def __init__(self, dump: str = _DUMP) -> None:
        self._dump = dump
        self.commands: list[str] = []

    async def shell(self, command: str, *, timeout_s: float = 15.0) -> TransportResult:
        self.commands.append(command)
        if command.startswith("uiautomator dump"):
            return TransportResult(True, self._dump, "")
        return TransportResult(True, "", "")

    async def is_connected(self) -> bool:
        return True


def test_parse_dump_extracts_nodes_with_centers() -> None:
    nodes = parse_uiautomator_dump(_DUMP)
    assert len(nodes) == 3
    settings = next(n for n in nodes if n.label == "Settings")
    assert settings.resource_id == "com.app:id/settings_btn"
    assert settings.cx == 200  # 100 + (300-100)//2
    assert settings.cy == 240  # 200 + (280-200)//2
    assert settings.clickable is True


def test_parse_dump_rejects_entity_expansion() -> None:
    evil = "<!DOCTYPE x [<!ENTITY boom 'aaaa'>]><hierarchy></hierarchy>"
    assert parse_uiautomator_dump(evil) == ()


def test_parse_dump_handles_garbage() -> None:
    assert parse_uiautomator_dump("not xml at all") == ()


async def test_perception_snapshot_lists_settings() -> None:
    adapter = AndroidPerceptionAdapter(FakeTransport())
    snap = await adapter.snapshot()
    labels = [e.label for e in snap.elements]
    assert "Settings" in labels
    assert snap.confidence > 0


async def test_tap_resolves_center_from_dump_not_guess() -> None:
    transport = FakeTransport()
    adapter = AndroidControlAdapter(transport)
    action = ControlAction(
        capability=ActionCapability.UI,
        operation="tap",
        target=TargetRef(strategy=TargetStrategy.TEXT, value="Settings"),
    )
    result = await adapter.dispatch(action)
    assert result.ok is True
    taps = [c for c in transport.commands if c.startswith("input tap")]
    assert taps == ["input tap 200 240"]


async def test_tap_by_resource_id() -> None:
    transport = FakeTransport()
    adapter = AndroidControlAdapter(transport)
    action = ControlAction(
        capability=ActionCapability.UI,
        operation="tap",
        target=TargetRef(strategy=TargetStrategy.RESOURCE_ID, value="com.app:id/settings_btn"),
    )
    result = await adapter.dispatch(action)
    assert result.ok is True
    assert "input tap 200 240" in transport.commands


async def test_tap_missing_target_fails_honestly() -> None:
    adapter = AndroidControlAdapter(FakeTransport())
    action = ControlAction(
        capability=ActionCapability.UI,
        operation="tap",
        target=TargetRef(strategy=TargetStrategy.TEXT, value="DoesNotExist"),
    )
    result = await adapter.dispatch(action)
    assert result.ok is False
    assert "not found" in (result.error or "")


async def test_launch_validates_package_name() -> None:
    transport = FakeTransport()
    adapter = AndroidControlAdapter(transport)
    good = await adapter.dispatch(
        ControlAction(
            capability=ActionCapability.APP, operation="launch", arguments={"package": "com.android.settings"}
        )
    )
    assert good.ok is True
    bad = await adapter.dispatch(
        ControlAction(capability=ActionCapability.APP, operation="launch", arguments={"package": "com.app; rm -rf /"})
    )
    assert bad.ok is False


async def test_type_text_rejects_shell_metacharacters() -> None:
    adapter = AndroidControlAdapter(FakeTransport())
    bad = await adapter.dispatch(
        ControlAction(capability=ActionCapability.UI, operation="type_text", arguments={"text": "hello'; drop table"})
    )
    assert bad.ok is False
    good = await adapter.dispatch(
        ControlAction(capability=ActionCapability.UI, operation="type_text", arguments={"text": "hello world"})
    )
    assert good.ok is True
