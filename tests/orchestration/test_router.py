"""SUPERSEDED — the Router (a per-task model call to classify capabilities) was
replaced by the deterministic ``capabilities_from_intent`` projection in
``atlas.orchestration.understanding``. Its two behaviours now live in
``test_phase2_understanding.py``:

  * capability parsing  -> test_capabilities_projection_* (no model call at all)
  * cautious-on-failure -> test_bad_json_fails_toward_caution_not_toward_speed

This file is kept only because the deletion could not run while the Bash safety
classifier was unavailable; it intentionally imports nothing and defines no
tests so collection stays green. Delete it (and ``orchestration/router.py``)
once ``rm`` is available again.
"""

from __future__ import annotations
