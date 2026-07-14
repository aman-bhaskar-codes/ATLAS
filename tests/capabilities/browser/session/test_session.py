import pytest

from atlas.capabilities.browser.page.page_manager import PageManager
from atlas.capabilities.browser.registry.provider_registry import ProviderRegistry
from atlas.capabilities.browser.session.manager import SessionManager
from atlas.capabilities.browser.session.pool import BrowserPool
from tests.capabilities.browser.providers.fake import FakeBrowserProvider


@pytest.fixture
def provider_registry():
    registry = ProviderRegistry()
    registry.register(FakeBrowserProvider(), preference=100)
    return registry


@pytest.fixture
def ids():
    from atlas.infra.ids import UuidGenerator
    return UuidGenerator()


@pytest.fixture
def pool(provider_registry):
    return BrowserPool(provider_registry, max_concurrent=2)


@pytest.fixture
def session_manager(pool, ids):
    return SessionManager(pool, ids)


@pytest.fixture
def page_manager(pool):
    return PageManager(pool)


@pytest.mark.asyncio
async def test_acquire_and_release_session(session_manager, pool):
    # Acquire session
    session = await session_manager.acquire(incognito=True)
    assert session is not None
    assert session.state.auth_state.value == "anonymous"

    # Verify pool state
    assert len(pool._sessions) == 1
    assert session.id in pool._sessions

    provider = pool.get_provider(session.id)
    assert provider.name == "fake"
    assert len(provider.sessions) == 1

    # Release session
    await session_manager.release(session.id)
    assert len(pool._sessions) == 0
    assert len(provider.sessions) == 0


@pytest.mark.asyncio
async def test_max_concurrent_sessions(session_manager, pool):
    # Acquire up to max (2)
    s1 = await session_manager.acquire()
    s2 = await session_manager.acquire()
    
    assert len(pool._sessions) == 2

    # Third should fail
    from atlas.capabilities.browser.errors import SessionError
    with pytest.raises(SessionError, match="Max concurrent browser sessions reached"):
        await session_manager.acquire()

    await session_manager.release(s1.id)
    await session_manager.release(s2.id)


@pytest.mark.asyncio
async def test_page_manager(session_manager, page_manager, pool):
    session = await session_manager.acquire()
    
    handle = await page_manager.new_page(session.id)
    assert handle.session_id == session.id
    assert handle.tab_id is not None

    provider = pool.get_provider(session.id)
    provider_session_id = session.state.session_id
    assert handle.tab_id in provider.sessions[provider_session_id]["tabs"]

    await page_manager.close_page(handle)
    assert handle.tab_id not in provider.sessions[provider_session_id]["tabs"]

    await session_manager.release(session.id)
