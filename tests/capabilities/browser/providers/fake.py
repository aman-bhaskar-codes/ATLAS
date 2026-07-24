"""FakeBrowserProvider for zero-flake end-to-end testing of the session layer, gates, and recovery."""
from __future__ import annotations

import uuid
from typing import Any

from atlas.capabilities.browser.domain.locator import Locator
from atlas.capabilities.browser.providers.base import BrowserProvider
from atlas.capabilities.browser.providers.capabilities import ProviderCapabilities


class FakeBrowserProvider(BrowserProvider):
    def __init__(self) -> None:
        self.name = "fake"
        self.is_local = True
        self.capabilities = ProviderCapabilities(
            persistent_profiles=True,
            incognito=True,
            pdf_export=False,
            request_interception=False,
            har_capture=False,
            file_upload=True,
            downloads=True,
            multi_tab=True,
            device_emulation=False,
            remote=False,
            vision_native=False
        )
        self.sessions: dict[str, dict[str, Any]] = {}  # session_id -> {tabs, etc}
        self.pages: dict[str, dict[str, Any]] = {}     # tab_id -> {url, dom}
        self.log: list[str] = []

    async def launch(self, *, profile: str | None, incognito: bool,
                     sandbox_spec: Any) -> str:
        sid = f"fs_{uuid.uuid4().hex[:8]}"
        self.sessions[sid] = {"tabs": []}
        self.log.append(f"launch:{sid}")
        return sid

    async def attach(self, endpoint: str) -> str:
        raise NotImplementedError()

    async def health(self, session_id: str) -> bool:
        return session_id in self.sessions

    async def close(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)
        self.log.append(f"close:{session_id}")

    async def new_tab(self, session_id: str) -> str:
        tid = f"ft_{uuid.uuid4().hex[:8]}"
        self.sessions[session_id]["tabs"].append(tid)
        self.pages[tid] = {"url": "about:blank", "html": "<html></html>"}
        self.log.append(f"new_tab:{tid}")
        return tid

    async def list_tabs(self, session_id: str) -> list[str]:
        return self.sessions.get(session_id, {}).get("tabs", [])  # type: ignore

    async def close_tab(self, session_id: str, tab_id: str) -> None:
        if session_id in self.sessions and tab_id in self.sessions[session_id]["tabs"]:
            self.sessions[session_id]["tabs"].remove(tab_id)
        self.pages.pop(tab_id, None)
        self.log.append(f"close_tab:{tab_id}")

    async def goto(self, session_id: str, tab_id: str, url: str) -> None:
        if tab_id in self.pages:
            self.pages[tab_id]["url"] = url
        self.log.append(f"goto:{url}")

    async def back(self, session_id: str, tab_id: str) -> None:
        self.log.append("back")

    async def forward(self, session_id: str, tab_id: str) -> None:
        self.log.append("forward")

    async def reload(self, session_id: str, tab_id: str) -> None:
        self.log.append("reload")

    async def dom_snapshot(self, session_id: str, tab_id: str) -> Any:
        return {"nodes": []}

    async def accessibility_tree(self, session_id: str, tab_id: str) -> Any:
        return {"role": "WebArea", "name": "Fake Page"}

    async def query(self, session_id: str, tab_id: str, locator: Locator) -> list[Any]:
        return [{"id": "fake_element"}]

    async def content_html(self, session_id: str, tab_id: str) -> str:
        return self.pages.get(tab_id, {}).get("html", "")  # type: ignore

    async def eval_readonly(self, session_id: str, tab_id: str, expr: str) -> Any:
        return None

    async def click(self, session_id: str, tab_id: str, locator: Locator) -> None:
        self.log.append(f"click:{locator.value}")

    async def type_text(self, session_id: str, tab_id: str, locator: Locator, text: str) -> None:
        self.log.append(f"type:{locator.value}:{text}")

    async def press(self, session_id: str, tab_id: str, key: str) -> None:
        self.log.append(f"press:{key}")

    async def scroll(self, session_id: str, tab_id: str, x: int, y: int) -> None:
        self.log.append(f"scroll:{x},{y}")

    async def submit(self, session_id: str, tab_id: str, locator: Locator) -> None:
        self.log.append(f"submit:{locator.value}")

    async def set_files(self, session_id: str, tab_id: str, locator: Locator, paths: list[str]) -> None:
        self.log.append(f"set_files:{locator.value}:{paths}")

    async def screenshot(self, session_id: str, tab_id: str, *, full_page: bool, clip: Any | None) -> bytes:
        return b"fake_screenshot_data"

    async def pdf(self, session_id: str, tab_id: str) -> bytes:
        return b"fake_pdf_data"

    async def start_network_capture(self, session_id: str, tab_id: str) -> None:
        self.log.append("start_network_capture")

    async def drain_network_events(self, session_id: str, tab_id: str) -> list[Any]:
        return []

    async def await_download(self, session_id: str, tab_id: str) -> tuple[str, str]:
        return ("/tmp/fake.pdf", "https://example.com/fake.pdf")

    async def get_cookies(self, session_id: str) -> list[Any]:
        return []

    async def set_cookies(self, session_id: str, cookies: list[Any]) -> None:
        pass

    async def get_storage_state(self, session_id: str) -> Any:
        return {}

    async def set_storage_state(self, session_id: str, state: Any) -> None:
        pass
