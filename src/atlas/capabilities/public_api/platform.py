"""PublicAPIPlatform — the facade for discovery → validation → execution.

Hard rules enforced here (Phases 22-29):
* DISCOVERED/UNVALIDATED connectors NEVER execute (Scenario 6).
* Execution returns normalized, provenance-tagged, UNTRUSTED data.
* Discovery is cheap keyword retrieval — the corpus never enters prompts.
"""

from __future__ import annotations

from atlas.infra.logging import get_logger

from .catalog import PublicAPICatalog
from .connector import ConnectorRegistry, ConnectorStatus
from .normalization import NormalizedAPIResult, normalize_response
from .retrieval import Candidate, CapabilityRetriever
from .validation import ConnectorValidator, ValidationResult

_log = get_logger("atlas.public_api")


class ConnectorNotExecutableError(RuntimeError):
    """Raised when execution is attempted against a non-validated connector."""


class PublicAPIPlatform:
    def __init__(
        self,
        catalog: PublicAPICatalog,
        connectors: ConnectorRegistry,
        validator: ConnectorValidator,
        retriever: CapabilityRetriever,
    ) -> None:
        self._catalog = catalog
        self._connectors = connectors
        self._validator = validator
        self._retriever = retriever
        self._fetcher = validator._fetcher  # same transport, one place to swap

    @property
    def catalog(self) -> PublicAPICatalog:
        return self._catalog

    @property
    def connectors(self) -> ConnectorRegistry:
        return self._connectors

    # --- discovery ---

    def discover(self, intent: str, *, limit: int = 5) -> list[Candidate]:
        return self._retriever.retrieve(intent, limit=limit)

    def explain_limitation(self, intent: str) -> str:
        return self._retriever.explain_limitation(intent)

    # --- validation (the ONLY path to execution rights) ---

    async def validate(self, api_id: str, *, probe_path: str = "") -> ValidationResult:
        entry = self._catalog.get(api_id)
        if entry is None:
            return ValidationResult(False, f"unknown api_id: {api_id}")
        record = self._connectors.get(api_id)
        if record and record.status is ConnectorStatus.DISABLED:
            return ValidationResult(False, "connector disabled by policy")
        self._connectors.promote(api_id, ConnectorStatus.EXPERIMENTAL, note="validation probe started")
        result = await self._validator.validate(entry, probe_path=probe_path)
        if result.ok:
            self._connectors.promote(api_id, ConnectorStatus.VALIDATED, note=result.note)
            _log.info("public_api.validated", api_id=api_id, note=result.note)
        else:
            self._connectors.promote(api_id, ConnectorStatus.DISCOVERED, note=result.note)
        return result

    # --- execution ---

    async def execute(
        self,
        api_id: str,
        *,
        path: str = "",
        params: dict[str, str] | None = None,
    ) -> NormalizedAPIResult:
        entry = self._catalog.get(api_id)
        if entry is None:
            raise ConnectorNotExecutableError(f"unknown api_id: {api_id}")
        record = self._connectors.get(api_id)
        if record is None or not record.executable:
            status = record.status.value if record else "unknown"
            # Scenario 6: unknown/discovered APIs stay DISCOVERED / UNVALIDATED.
            raise ConnectorNotExecutableError(
                f"connector {api_id} is {status.upper()}/UNVALIDATED — "
                "execution refused until connector validation promotes it"
            )
        url = entry.url.rstrip("/") + path
        if params:
            from urllib.parse import urlencode

            url = f"{url}?{urlencode(params)}"
        resp = await self._fetcher.get(url, timeout_s=15.0)
        return normalize_response(
            resp.body,
            provider=entry.name,
            url=url,
            status_code=resp.status,
            content_type=resp.content_type,
        )
