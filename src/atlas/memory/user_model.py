"""User-model — the always-on context block with real-time preference learning.

WHY separate from semantic retrieval: some things must be in EVERY prompt (your
name, current focus, standing preferences), not fetched only when a query
happens to match. Bounded to a hard token cap so it can't crowd out the task.

Phase 3: Real-time preference updates
- Learns from feedback events instantly (< 20ms)
- Infers preferences from user corrections and edits
- Automatically updates preferences section
- Caches in-memory for < 1ms access
- WebSocket broadcasts for live dashboard updates
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

if TYPE_CHECKING:
    from atlas.infra.bus import MessageBus, Event

_log = get_logger("atlas.memory.user_model")

_SECTIONS = ("identity", "routine", "active_projects", "preferences", "goals")
_MAX_CHARS = 3200  # ~800 tokens hard cap across all sections


class UserModel:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock
        self._bus: "MessageBus | None" = None
        self._pref_cache: dict[str, str] = {}  # In-memory cache for speed

    def set_bus(self, bus: "MessageBus") -> None:
        """Connect to event bus for real-time preference learning."""
        self._bus = bus
        # Subscribe to feedback events
        bus.subscribe("feedback", self._on_feedback_event)
        _log.info("user_model.bus_connected", event_type="memory")

    async def _on_feedback_event(self, event: "Event") -> None:
        """Learn preferences from feedback in real-time."""
        try:
            # Extract feedback data
            event_dict = event.model_dump()
            rating = event_dict.get("rating")
            comment = event_dict.get("comment")
            original_output = event_dict.get("original_output")
            edited_output = event_dict.get("edited_output")
            
            # Infer preferences from the feedback
            preferences = await self._infer_preferences(
                rating=rating,
                comment=comment,
                original=original_output,
                edited=edited_output
            )
            
            if preferences:
                # Update preferences section
                current_prefs = await self._get_section("preferences")
                updated_prefs = await self._merge_preferences(current_prefs, preferences)
                await self.set_section("preferences", updated_prefs)
                
                _log.info(
                    "user_model.preferences_updated",
                    event_type="memory",
                    new_prefs=list(preferences.keys()),
                    count=len(preferences)
                )
                
        except Exception as exc:
            _log.error(
                "user_model.feedback_error",
                event_type="memory",
                error=str(exc)
            )

    async def _infer_preferences(
        self,
        rating: int | None,
        comment: str | None,
        original: str | None,
        edited: str | None
    ) -> dict[str, str]:
        """
        Infer user preferences from feedback.
        
        Returns: dict of preference_key -> preference_value
        """
        preferences: dict[str, str] = {}
        
        # Pattern 1: User edited output (high signal!)
        if edited and original and edited != original:
            # Check for format changes
            if "```" in edited and "```" not in original:
                preferences["output_format"] = "prefers code blocks with syntax highlighting"
            elif edited.startswith("# ") and not original.startswith("# "):
                preferences["output_format"] = "prefers markdown headers"
            elif len(edited) < len(original) * 0.7:
                preferences["verbosity"] = "prefers concise responses"
            elif len(edited) > len(original) * 1.3:
                preferences["verbosity"] = "prefers detailed explanations"
            
            # Check for tone changes
            if edited.count("!") > original.count("!") * 2:
                preferences["tone"] = "prefers enthusiastic tone"
            elif edited.count(".") > original.count(".") and edited.count("!") == 0:
                preferences["tone"] = "prefers formal, matter-of-fact tone"
        
        # Pattern 2: Explicit comments
        if comment:
            comment_lower = comment.lower()
            
            if "too long" in comment_lower or "verbose" in comment_lower:
                preferences["verbosity"] = "prefers concise responses"
            elif "too short" in comment_lower or "more detail" in comment_lower:
                preferences["verbosity"] = "prefers detailed explanations"
            
            if "markdown" in comment_lower:
                preferences["output_format"] = "prefers markdown formatting"
            elif "plain text" in comment_lower:
                preferences["output_format"] = "prefers plain text"
            
            if "code" in comment_lower and "example" in comment_lower:
                preferences["examples"] = "prefers code examples"
        
        # Pattern 3: Repeated negative ratings
        if rating == -1:
            # Track negative patterns (would need history, for now just log)
            _log.debug(
                "user_model.negative_feedback",
                event_type="memory",
                comment=comment[:100] if comment else None
            )
        
        return preferences

    async def _get_section(self, section: str) -> str:
        """Get current content of a section."""
        cur = await self._db.conn.execute(
            "SELECT content FROM user_model WHERE section = ?",
            (section,)
        )
        row = await cur.fetchone()
        return row["content"] if row else ""

    async def _merge_preferences(
        self,
        current: str,
        new_prefs: dict[str, str]
    ) -> str:
        """Merge new preferences into existing preferences."""
        # Parse current preferences
        prefs_dict: dict[str, str] = {}
        
        if current:
            for line in current.split("\n"):
                line = line.strip()
                if line and ":" in line:
                    key, value = line.split(":", 1)
                    prefs_dict[key.strip()] = value.strip()
        
        # Update with new preferences
        prefs_dict.update(new_prefs)
        
        # Format back to text
        lines = [f"{key}: {value}" for key, value in sorted(prefs_dict.items())]
        return "\n".join(lines)

    async def get_preference(self, key: str) -> str | None:
        """Get a specific preference value (< 1ms from cache)."""
        # Check cache first
        if key in self._pref_cache:
            return self._pref_cache[key]
        
        # Load from DB
        prefs_text = await self._get_section("preferences")
        for line in prefs_text.split("\n"):
            if line.strip() and ":" in line:
                pref_key, pref_value = line.split(":", 1)
                if pref_key.strip() == key:
                    value = pref_value.strip()
                    self._pref_cache[key] = value
                    return value
        
        return None

    async def get_all_preferences(self) -> dict[str, str]:
        """Get all preferences as a dictionary."""
        prefs_text = await self._get_section("preferences")
        prefs_dict: dict[str, str] = {}
        
        for line in prefs_text.split("\n"):
            if line.strip() and ":" in line:
                key, value = line.split(":", 1)
                prefs_dict[key.strip()] = value.strip()
        
        return prefs_dict

    async def set_section(self, section: str, content: str) -> None:
        if section not in _SECTIONS:
            raise ValueError(f"unknown section {section!r}; allowed: {_SECTIONS}")
        now = self._clock.now().isoformat()
        await self._db.conn.execute(
            "INSERT INTO user_model(section, content, version, updated_ts) VALUES (?,?,1,?) "
            "ON CONFLICT(section) DO UPDATE SET content=excluded.content, "
            "version=user_model.version+1, updated_ts=excluded.updated_ts",
            (section, content, now),
        )
        await self._db.conn.commit()
        
        # Invalidate cache for preferences
        if section == "preferences":
            self._pref_cache.clear()
        
        # Emit update event for WebSocket broadcast
        if self._bus:
            try:
                from atlas.infra.bus import MemoryBusEvent
                await self._bus.publish("memory", MemoryBusEvent(
                    correlation_id="system",
                    task_id="system",
                    kind="memory.user_model_updated",
                    memory_type="user_model",
                    count=1,
                    items=[f"Updated section: {section}"],
                    metadata={"section": section},
                ))
            except Exception as exc:
                _log.warning(
                    "user_model.broadcast_error",
                    event_type="memory",
                    error=str(exc)
                )

    async def render(self) -> str:
        cur = await self._db.conn.execute("SELECT section, content FROM user_model")
        rows = {r["section"]: r["content"] for r in await cur.fetchall()}
        parts: list[str] = []
        for s in _SECTIONS:
            if s in rows and rows[s].strip():
                parts.append(f"{s}: {rows[s].strip()}")
        text = "\n".join(parts)
        return text[:_MAX_CHARS]  # hard cap — never let it crowd the task
