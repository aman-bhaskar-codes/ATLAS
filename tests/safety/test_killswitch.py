"""Tests for kill switch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from atlas.safety.killswitch import KillSwitch


class TestKillSwitch:
    @pytest.fixture
    def flag_path(self, tmp_path: Path) -> Path:
        return tmp_path / "STOP.flag"

    @pytest.fixture
    def killswitch(self, flag_path: Path) -> KillSwitch:
        return KillSwitch(str(flag_path))

    def test_initially_inactive_when_no_flag(self, killswitch: KillSwitch, flag_path: Path) -> None:
        assert not flag_path.exists()
        assert killswitch.is_active() is False

    def test_active_when_flag_exists(self, killswitch: KillSwitch, flag_path: Path) -> None:
        flag_path.touch()
        assert killswitch.is_active() is True

    def test_trip_sets_in_memory_flag(self, killswitch: KillSwitch, flag_path: Path) -> None:
        killswitch.trip()
        assert killswitch.is_active() is True
        assert flag_path.exists()

    def test_trip_idempotent(self, killswitch: KillSwitch, flag_path: Path) -> None:
        killswitch.trip()
        killswitch.trip()
        assert killswitch.is_active() is True

    def test_reset_clears_flag(self, killswitch: KillSwitch, flag_path: Path) -> None:
        killswitch.trip()
        assert killswitch.is_active() is True
        killswitch.reset()
        assert killswitch.is_active() is False
        assert not flag_path.exists()

    def test_reset_without_trip(self, killswitch: KillSwitch, flag_path: Path) -> None:
        killswitch.reset()
        assert killswitch.is_active() is False

    def test_is_active_stays_true_after_trip_even_if_flag_removed(
        self, killswitch: KillSwitch, flag_path: Path
    ) -> None:
        killswitch.trip()
        flag_path.unlink()
        assert killswitch.is_active() is True

    def test_fail_safe_on_os_error(self, killswitch: KillSwitch, flag_path: Path) -> None:
        with patch.object(Path, "exists", side_effect=OSError("permission denied")):
            assert killswitch.is_active() is True
