import pytest

from atlas.orchestration.errors import ReasoningError
from atlas.orchestration.limits import ExecutionLimits, LimitCounter


def test_max_tool_calls() -> None:
    c = LimitCounter(ExecutionLimits(max_tool_calls=1))
    c.tick_tool()
    with pytest.raises(ReasoningError):
        c.tick_tool()

def test_max_tokens() -> None:
    c = LimitCounter(ExecutionLimits(max_tokens=10))
    with pytest.raises(ReasoningError):
        c.add_tokens(11)

def test_retry_budget() -> None:
    c = LimitCounter(ExecutionLimits(max_retries=2))
    assert c.tick_retry() and c.tick_retry() and not c.tick_retry()
