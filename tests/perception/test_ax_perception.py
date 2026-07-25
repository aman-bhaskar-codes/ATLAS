from atlas.perception.tool import AXPerceptionTool
from atlas.perception.types import PerceptionSource, ScreenState, UIElement


class FakeBackend:
    def __init__(self, state: ScreenState, avail: bool = True) -> None:
        self._state, self._avail = state, avail

    def available(self) -> bool:
        return self._avail

    def capture_frontmost(self) -> ScreenState:
        return self._state


_STATE = ScreenState(
    source=PerceptionSource.AX_TREE, app_name="Mail", window_title="Inbox",
    elements=(UIElement(role="button", label="Send", ax_path="window[0]/button[1]:Send"),),
)


async def test_perception_returns_state() -> None:
    tool = AXPerceptionTool(FakeBackend(_STATE))
    res = await tool.execute({})
    assert res.ok
    assert res.output["app_name"] == "Mail"


async def test_perception_unavailable_is_clean_error() -> None:
    tool = AXPerceptionTool(FakeBackend(_STATE, avail=False))
    res = await tool.execute({})
    assert not res.ok and "unavailable" in (res.error or "")


def test_summarize_is_bounded() -> None:
    text = _STATE.summarize()
    assert "Mail" in text and "Send" in text
