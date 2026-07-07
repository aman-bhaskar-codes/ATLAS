from datetime import UTC, datetime

import pytest

from atlas.memory.retrieval import Retriever
from atlas.memory.types import Episode, EpisodeKind, FactKind, SemanticFact


class FakeSem:
    async def semantic_search(self, query: str, k: int) -> list[SemanticFact]:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        return [SemanticFact(id="f1", text="prefers dark mode", kind=FactKind.PREFERENCE,
                             created_ts=now, updated_ts=now, salience=0.9)]


class FakeEpi:
    async def keyword_search(self, terms: list[str], limit: int) -> list[Episode]:
        return [Episode(correlation_id="c", ts=datetime(2026, 1, 1, tzinfo=UTC),
                        kind=EpisodeKind.MESSAGE, content="opened VS Code")]


class FakeUM:
    async def render(self) -> str:
        return "identity: Anti, BTech CSE"


@pytest.mark.asyncio
async def test_retrieval_always_includes_user_model() -> None:
    r = Retriever(semantic=FakeSem(), episodic=FakeEpi(), user_model=FakeUM())  # type: ignore
    ctx = await r.retrieve("what theme do I like")
    assert "Anti" in ctx.user_model
    assert any("dark mode" in f.text for f in ctx.facts)


@pytest.mark.asyncio
async def test_budget_is_respected() -> None:
    r = Retriever(semantic=FakeSem(), episodic=FakeEpi(), user_model=FakeUM(), token_budget=1)  # type: ignore
    ctx = await r.retrieve("x")
    # budget is 1 token, meaning only User Model is there, and NO facts or episodic
    assert len(ctx.facts) == 0
    assert len(ctx.recent_episodes) == 0
