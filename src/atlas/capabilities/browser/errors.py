"""Browser error taxonomy — typed so recovery/retry/provider-switch decisions are explicit."""
from __future__ import annotations

from atlas.capabilities.errors import CapabilityError


class BrowserError(CapabilityError): ...


class NavigationError(BrowserError):
    retryable = True


class ElementNotFound(BrowserError):  # noqa: N818
    retryable = True


class BrowserTimeout(BrowserError):  # noqa: N818
    retryable = True


class DownloadError(BrowserError):
    retryable = True


class UploadError(BrowserError): ...


class VisionError(BrowserError): ...


class SessionError(BrowserError):
    provider_switch_helps = True

class ProviderError(BrowserError): ...


class AuthenticationError(BrowserError):
    provider_switch_helps = True


class PopupError(BrowserError): ...


class NetworkError(BrowserError):
    retryable = True


class DOMError(BrowserError): ...


class RecoveryError(BrowserError): ...
