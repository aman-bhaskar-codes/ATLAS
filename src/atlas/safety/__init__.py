"""L1 Safety Engine — the reference monitor.

Every consequential action passes through here. By contract this package may
import atlas.infra and atlas.tools.base, but NEVER a concrete provider or a
concrete interface (only protocols, injected).
"""
