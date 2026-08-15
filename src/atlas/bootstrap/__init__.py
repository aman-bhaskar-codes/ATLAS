"""Bootstrap package — decomposes app.py's build() into focused modules.

Each module builds one concern and returns a typed dataclass. app.py's
build() delegates to each module in dependency order and combines results.
"""
