# Vision & visual evidence

Vision in ATLAS is a SUPPORTING sense, not the primary one. Structured
channels (DOM, accessibility trees, uiautomator dumps) are cheaper, more
reliable and privacy-friendlier. Pixels enter only when explicitly requested
or when structure genuinely fails.

## On-demand evidence

`PerceptionAdapter.visual_evidence(full_page=False)` returns
`VisualEvidence(format="png"|"jpeg", data=bytes, redacted=bool)` — or `None`
when no page/screen exists yet. It is deliberately NOT part of `snapshot()`:

- perception stays cheap on the hot loop (perceive runs before AND after
  every action)
- screenshots are captured only when deep evidence is requested (debugging,
  verification escalations, user-visible proof)

Per-substrate sources:

| Substrate | Source |
| --- | --- |
| browser | Playwright page screenshot (`capture_screenshot`) |
| macOS | screen capture via AX/screencapture path |
| Android | `adb exec-out screencap -p` |

## Vision grounding (future-first design)

`src/atlas/capabilities/computer_use/vision.py` defines the seam for visual
grounding without pretending it exists today:

```python
class VisionGrounder(Protocol):
    async def available(self) -> bool: ...
    async def ground(self, image: bytes, query: str) -> GroundingResult: ...
```

- `GroundingResult` carries `GroundingCandidate`s (bounds + confidence)
- `NullVisionGrounder` is the honest default: `available() → False`. When no
  grounder is attached, `image_ref` / coordinate-based targets simply cannot
  resolve — the engine reports the limitation instead of guessing.

This keeps `TargetStrategy.IMAGE_REF` and `COORDINATES` in the universal
vocabulary (they exist in `RESOLUTION_ORDER`, ranked LAST) while enforcing
"no grounding capability → no visual actions".

## Redaction before anything leaves the machine

`src/atlas/capabilities/computer_use/redaction.py`:

- `is_sensitive_field(label, value)` — field-name heuristics (password,
  token, ssn, …)
- `contains_secret_shape(text)` — value-shape heuristics (API keys, cards)
- `redact_snapshot(snapshot, policy=...)` — returns a NEW snapshot with
  sensitive labels/values replaced by `[REDACTED]`

`ComputerUseTool` redacts snapshots before they enter tool output, model
context or logs. Snapshots can also be flagged `sensitive=True` at surface
level (banking apps etc.), which `perception/sensitivity.py` classifies.

## Rules

1. Never claim what was seen from a screenshot unless structured perception
   corroborates it — text evidence comes from DOM/AX dumps, not OCR hopes.
2. Visual grounding candidates are just that: candidates. They must pass the
   same engine resolution + safety gates as any other target.
3. Screenshots are evidence artifacts bound to a correlation id — treated
   like any other audit record.
