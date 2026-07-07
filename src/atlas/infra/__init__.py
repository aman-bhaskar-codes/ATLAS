"""L0 infrastructure: mechanism, never policy.

Modules here provide the runtime substrate (config, ids, logging, bus, db,
gateway, lifecycle). By architectural contract (see importlinter.ini) nothing
in this package may import atlas.safety / atlas.tools / atlas.interfaces.
"""
