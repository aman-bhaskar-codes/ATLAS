"""Tests for sensitivity classification."""

from __future__ import annotations

from atlas.perception.sensitivity import _SENSITIVE_BUNDLE_HINTS, is_sensitive_app


class TestSensitiveBundleHints:
    def test_is_frozenset(self) -> None:
        assert isinstance(_SENSITIVE_BUNDLE_HINTS, frozenset)

    def test_contains_password_managers(self) -> None:
        assert "1password" in _SENSITIVE_BUNDLE_HINTS
        assert "bitwarden" in _SENSITIVE_BUNDLE_HINTS

    def test_contains_banking(self) -> None:
        assert "bank" in _SENSITIVE_BUNDLE_HINTS
        assert "banking" in _SENSITIVE_BUNDLE_HINTS

    def test_contains_messaging(self) -> None:
        assert "messages" in _SENSITIVE_BUNDLE_HINTS
        assert "whatsapp" in _SENSITIVE_BUNDLE_HINTS


class TestIsSensitiveApp:
    def test_none_returns_false(self) -> None:
        assert is_sensitive_app(None) is False

    def test_empty_string_returns_false(self) -> None:
        assert is_sensitive_app("") is False

    def test_password_manager_detected(self) -> None:
        assert is_sensitive_app("1Password") is True
        assert is_sensitive_app("Bitwarden") is True

    def test_banking_app_detected(self) -> None:
        assert is_sensitive_app("Chase Banking") is True
        assert is_sensitive_app("Bank of America") is True

    def test_messaging_app_detected(self) -> None:
        assert is_sensitive_app("Messages") is True
        assert is_sensitive_app("WhatsApp") is True

    def test_normal_app_not_sensitive(self) -> None:
        assert is_sensitive_app("Safari") is False
        assert is_sensitive_app("Notes") is False
        assert is_sensitive_app("Calculator") is False

    def test_case_insensitive(self) -> None:
        assert is_sensitive_app("1PASSWORD") is True
        assert is_sensitive_app("BITWARDEN") is True
