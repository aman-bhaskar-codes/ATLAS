import pytest

from atlas.capabilities.browser.domain.session import ContextSpec
from atlas.capabilities.browser.providers.cdp_provider import CDPProvider
from atlas.capabilities.browser.registry.provider_registry import ProviderRegistry
from atlas.capabilities.browser.session.pool import BrowserPool
from atlas.capabilities.browser.session.manager import SessionManager
class FakeIdGenerator:
    def __init__(self):
        self.task_id = "test-task"

    def generate(self, prefix: str = "") -> str:
        return f"{prefix}-123"

@pytest.mark.asyncio
async def test_provider_swap_to_cdp():
    registry = ProviderRegistry()
    cdp = CDPProvider()
    
    # Register cdp with highest preference
    registry.register(cdp, preference=100)
    
    pool = BrowserPool(registry)
    ids = FakeIdGenerator()
    session_manager = SessionManager(pool=pool, ids=ids)
    
    session = await session_manager.acquire(profile="test", incognito=True)
    
    provider = pool.get_provider(session.id)
    assert provider.name == "cdp"
    
    # We don't necessarily need to perform a full navigation in the test, 
    # but the provider should have successfully launched (and thus connected via CDP).
    assert provider._browser is not None
    
    await session_manager.release(session.id)
    await provider._cleanup()
    assert provider._browser is None
