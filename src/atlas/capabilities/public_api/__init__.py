"""Public-API capability discovery.

Funnel (deliberate design — NOT "thousands of tools in every prompt"):

    public-apis corpus → Capability Catalog (static seed, synced offline)
        → Relevance Retrieval (intent → top-k candidates)
        → Candidate Connector (DISCOVERED, never executed)
        → Validation (probe + safety classification)
        → Capability Registry (VALIDATED/AVAILABLE only)
        → only relevant capabilities enter planning

External API output is UNTRUSTED DATA, never instructions.
"""

from .catalog import CatalogEntry, PublicAPICatalog
from .connector import ConnectorRecord, ConnectorRegistry, ConnectorStatus
from .platform import PublicAPIPlatform
from .retrieval import CapabilityRetriever

__all__ = [
    "CapabilityRetriever",
    "CatalogEntry",
    "ConnectorRecord",
    "ConnectorRegistry",
    "ConnectorStatus",
    "PublicAPICatalog",
    "PublicAPIPlatform",
]
