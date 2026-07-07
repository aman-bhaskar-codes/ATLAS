"""L5 interfaces — human and push I/O.

By contract, interfaces may import safety + infra, but safety must NEVER import
interfaces. Confirmers/notifiers are injected INTO the engine, not imported by
it.
"""
