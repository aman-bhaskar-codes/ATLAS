"""Computer-use + public-API bootstrap — attach bodies based on real detection.

WHY a dedicated builder: substrate availability is an ENVIRONMENT FACT, not a
config flag. EnvironmentDetector probes the machine; adapters are attached
only for what actually exists. A missing body is a normal state — the engine
then explains limitations honestly instead of faking attempts.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.capabilities.browser.platform import BrowserPlatform
from atlas.capabilities.computer_use.adapters.android import AndroidControlAdapter, AndroidPerceptionAdapter
from atlas.capabilities.computer_use.adapters.android_transport import ADBTransport
from atlas.capabilities.computer_use.adapters.browser import (
    BrowserContext,
    BrowserControlAdapter,
    BrowserPerceptionAdapter,
)
from atlas.capabilities.computer_use.adapters.macos import MacOSControlAdapter, MacOSPerceptionAdapter
from atlas.capabilities.computer_use.engine import ComputerUseEngine
from atlas.capabilities.computer_use.environment import EnvironmentDetector, EnvironmentReport
from atlas.capabilities.computer_use.telemetry import ComputerUseTelemetry
from atlas.capabilities.computer_use.tool import ComputerUseTool
from atlas.capabilities.public_api import (
    CapabilityRetriever,
    ConnectorRegistry,
    PublicAPICatalog,
    PublicAPIPlatform,
)
from atlas.capabilities.public_api.validation import ConnectorValidator, HttpFetcher, HttpxFetcher
from atlas.control.contracts import ControlAdapter
from atlas.control.osascript import OsascriptRunner
from atlas.infra.logging import get_logger
from atlas.perception.contracts import PerceptionAdapter, Substrate

_log = get_logger("atlas.bootstrap.computer_use")


@dataclass
class ComputerUseComponents:
    report: EnvironmentReport
    engine: ComputerUseEngine
    tool: ComputerUseTool
    public_api: PublicAPIPlatform


async def build_computer_use(
    *,
    browser_platform: BrowserPlatform | None = None,
    fetcher: HttpFetcher | None = None,
) -> ComputerUseComponents:
    """Detect the environment and attach every body that really exists."""
    detector = EnvironmentDetector()
    report = await detector.detect()

    perception: dict[Substrate, PerceptionAdapter] = {}
    control: dict[Substrate, ControlAdapter] = {}

    if report.available(Substrate.MACOS):
        from atlas.perception.macos_ax import MacOSAXBackend

        backend = MacOSAXBackend()
        perception[Substrate.MACOS] = MacOSPerceptionAdapter(backend)
        control[Substrate.MACOS] = MacOSControlAdapter(OsascriptRunner())

    if report.available(Substrate.BROWSER) and browser_platform is not None:
        ctx = BrowserContext(browser_platform)
        perception[Substrate.BROWSER] = BrowserPerceptionAdapter(ctx)
        control[Substrate.BROWSER] = BrowserControlAdapter(ctx)

    if report.available(Substrate.ANDROID):
        transport = ADBTransport(serial=report.android_devices[0] if report.android_devices else None)
        perception[Substrate.ANDROID] = AndroidPerceptionAdapter(transport)
        control[Substrate.ANDROID] = AndroidControlAdapter(transport)

    engine = ComputerUseEngine(perception, control, telemetry=ComputerUseTelemetry())
    tool = ComputerUseTool(engine)

    # Public-API funnel: catalog → retrieval → validation-gated execution.
    catalog = PublicAPICatalog.load_default()
    connectors = ConnectorRegistry(catalog)
    validator = ConnectorValidator(fetcher or HttpxFetcher())
    retriever = CapabilityRetriever(catalog, connectors)
    public_api = PublicAPIPlatform(catalog, connectors, validator, retriever)

    _log.info(
        "computer_use.ready",
        event_type="lifecycle",
        bodies=sorted(s.value for s in perception),
        catalog_apis=len(catalog),
    )
    return ComputerUseComponents(report=report, engine=engine, tool=tool, public_api=public_api)
