import pytest

from atlas.orchestration.errors import ValidationError
from atlas.orchestration.parser import ResponseParser
from atlas.orchestration.validator import OutputValidator


def test_parses_tool_call() -> None:
    _, a = ResponseParser().parse(
        '{"thought":"read it","confidence":0.9,"action":'
        '{"kind":"tool_call","tool":"filesystem","operation":"read","args":{"path":"/x"}}}', 1)
    assert a.kind == "tool_call" and a.tool == "filesystem"
    OutputValidator().validate(a)


def test_garbage_fails_closed_to_ask_user() -> None:
    _, a = ResponseParser().parse("total nonsense", 1)
    assert a.kind == "ask_user"


def test_validator_rejects_incomplete_tool_call() -> None:
    from atlas.orchestration.types import Action
    with pytest.raises(ValidationError):
        OutputValidator().validate(Action(step=1, kind="tool_call", tool="fs"))  # no operation
