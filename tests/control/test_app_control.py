from atlas.control.osascript import ScriptResult
from atlas.control.tool import AppControlTool


class FakeRunner:
    def __init__(self, result: ScriptResult) -> None:
        self._result = result
        self.last_script: str | None = None

    async def run(self, script: str, *, timeout_s: float = 15.0) -> ScriptResult:
        self.last_script = script
        return self._result


async def test_unknown_intent_denied():
    tool = AppControlTool(FakeRunner(ScriptResult(True, "", "", 0)))
    res = await tool.execute({"intent": "rm_everything"})
    assert not res.ok and "not allowlisted" in (res.error or "")


async def test_missing_param_rejected():
    tool = AppControlTool(FakeRunner(ScriptResult(True, "", "", 0)))
    res = await tool.execute({"intent": "open_app"})  # needs 'app'
    assert not res.ok and "missing params" in (res.error or "")


async def test_open_app_renders_and_runs():
    runner = FakeRunner(ScriptResult(True, "", "", 0))
    tool = AppControlTool(runner)
    res = await tool.execute({"intent": "open_app", "app": "Spotify"})
    assert res.ok
    assert 'tell application "Spotify" to activate' in (runner.last_script or "")
    assert res.side_effects and res.side_effects[0].kind == "app_control"


async def test_escaping_blocks_quote_injection():
    runner = FakeRunner(ScriptResult(True, "", "", 0))
    tool = AppControlTool(runner)
    await tool.execute({"intent": "open_app", "app": 'Evil" to quit\ntell application "Finder'})
    # the injected quote must be escaped, not break out of the string literal
    assert '\\"' in (runner.last_script or "")


async def test_read_intent_has_no_side_effect():
    tool = AppControlTool(FakeRunner(ScriptResult(True, "Bohemian Rhapsody", "", 0)))
    res = await tool.execute({"intent": "music_current"})
    assert res.ok and res.side_effects == ()
