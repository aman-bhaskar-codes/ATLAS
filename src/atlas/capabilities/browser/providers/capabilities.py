"""WHY: not every backend supports everything (a remote cloud browser may lack a
file chooser; WebKit's PDF differs). Providers DECLARE capabilities as data so the
provider registry can pick one that supports the requested verb, exactly like the
Phase-5 capability router picks a model by declared capabilities."""
from __future__ import annotations

from pydantic import BaseModel


class ProviderCapabilities(BaseModel):
    model_config = {"frozen": True}
    persistent_profiles: bool = True
    incognito: bool = True
    pdf_export: bool = True
    request_interception: bool = True
    har_capture: bool = True
    file_upload: bool = True
    downloads: bool = True
    multi_tab: bool = True
    device_emulation: bool = True
    remote: bool = False        # cloud browser (Browserbase/Steel)
    vision_native: bool = False # provider-side element detection
