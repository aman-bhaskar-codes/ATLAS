from typing import Any

from atlas.orchestration.context_builder import ContextBuilder


class FakeRetr:
    async def retrieve(self, q: str) -> Any:
        class R:
            user_model = "identity: Anti"
            facts: tuple[Any, ...] = ()
        return R()


class FakeWorking:
    def recent(self, n: int) -> tuple[Any, ...]: return ()


async def test_budget_trims_negotiable_layers() -> None:
    cb = ContextBuilder(
        retriever=FakeRetr(),  # type: ignore[arg-type]
        working=FakeWorking(),  # type: ignore[arg-type]
        system_prompt="SYS", token_budget=5,
    )
    out = await cb.build("hi", safety_constraints="deny-by-default", tool_catalog="tools: x")
    assert "SYS" in out  # priority-0 system layer always kept
