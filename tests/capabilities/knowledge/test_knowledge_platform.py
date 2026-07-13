from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.capabilities.domain.knowledge import KnowledgeIntent, KnowledgeItem, KnowledgeQuery
from atlas.capabilities.platforms.knowledge_platform import KnowledgePlatform
from atlas.infra.ids import CorrelationId


@pytest.mark.asyncio
async def test_knowledge_platform_routing() -> None:
    router = AsyncMock()
    gateway = AsyncMock()
    episodic = AsyncMock()
    ids = AsyncMock()
    clock = MagicMock()
    clock.now.return_value = datetime.now(UTC)
    
    official_provider = AsyncMock()
    official_provider.name = "rss:mock"
    official_provider.search.return_value = [
        KnowledgeItem(title="Official News", snippet="The mock news", 
                      provenance=Provenance(provider="rss:mock", source_kind=SourceKind.OFFICIAL, 
                                            uri="http://mock", retrieved_ts=datetime.now(UTC)))
    ]
    
    platform = KnowledgePlatform(
        router=router, gateway=gateway, episodic=episodic, ids=ids, clock=clock,
        official=[official_provider], web=[], memory_source=AsyncMock(), parametric=AsyncMock()
    )
    
    router.classify.return_value = KnowledgeIntent.LIVE
    gateway.complete.return_value = type("R", (), {
        "text": "The mock news says this."
    })()
    
    ans = await platform.obtain_knowledge(KnowledgeQuery(text="What's the news?"), CorrelationId("corrid"))
    
    assert ans.text == "The mock news says this."
    assert len(ans.sources) == 1
    assert ans.sources[0].title == "Official News"
    assert episodic.record.called
