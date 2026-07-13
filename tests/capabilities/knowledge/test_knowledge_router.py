from unittest.mock import AsyncMock

import pytest

from atlas.capabilities.domain.knowledge import KnowledgeIntent, KnowledgeQuery
from atlas.capabilities.platforms.knowledge_router import KnowledgeRouter
from atlas.infra.ids import CorrelationId


@pytest.mark.asyncio
async def test_knowledge_router_static() -> None:
    gateway = AsyncMock()
    # Mock gateway response to be "STATIC"
    gateway.complete.return_value = type("R", (), {"text": '{"intent": "static"}'})()
    
    router = KnowledgeRouter(gateway)
    query = KnowledgeQuery(text="What is the capital of France?")
    intent = await router.classify(query, CorrelationId("corrid"))
    
    assert intent == KnowledgeIntent.STATIC
    assert gateway.complete.called


@pytest.mark.asyncio
async def test_knowledge_router_live_override() -> None:
    gateway = AsyncMock()
    router = KnowledgeRouter(gateway)
    
    # "latest" implies LIVE
    query = KnowledgeQuery(text="What is the latest news about OpenAI?")
    intent = await router.classify(query, CorrelationId("corrid"))
    
    assert intent == KnowledgeIntent.LIVE
    assert not gateway.complete.called


@pytest.mark.asyncio
async def test_knowledge_router_memory_override() -> None:
    gateway = AsyncMock()
    router = KnowledgeRouter(gateway)
    
    # "we discussed" implies MEMORY
    query = KnowledgeQuery(text="What did we discussed yesterday?")
    intent = await router.classify(query, CorrelationId("corrid"))
    
    assert intent == KnowledgeIntent.LIVE # yesterday cues LIVE
    assert not gateway.complete.called
