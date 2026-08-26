"""Authentication is wired but opt-in: absent keys change nothing, set keys enforce.

THE BUG THIS PINS: ``auth.py`` was fully implemented and imported by NO route.
``require_principal`` and ``require_admin`` existed, ``ATLAS_API_KEYS`` was a real
setting, ``ro:`` was a documented prefix — and none of it ran. Three consequences:

* setting ``ATLAS_API_KEYS`` did nothing at all, so an operator who followed the
  documentation and exposed the port believed they had authentication;
* ``request.state.principal`` was never set, so ``rate_limit()``'s per-principal
  branch was dead code and every caller shared one per-IP bucket; and
* the readonly role was parsed and then ignored, making the module docstring's
  "READONLY keys can read but never mutate" false.

The dependency is now attached at router-include time. The critical half of this
test file is therefore the FIRST test: with no keys configured the local workflow
must be byte-identical to before, or wiring auth by default is a regression.

SECRET HYGIENE: assertions below compare ``key_id`` and ``role`` only. ``key_id``
is a deliberate 8-char prefix, never the key. No test asserts on, logs, or prints
a key value.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from atlas.interfaces.api.app import create_app
from atlas.interfaces.api.auth import parse_api_keys
from tests.api.conftest import app_client

_ENV_KEYS = "ATLAS_API_KEYS"

_ADMIN_KEY = "admin-key-0123456789abcdef"
_READONLY_KEY = "readonly-key-0123456789abcdef"
_KEYS_ENV = f"{_ADMIN_KEY},ro:{_READONLY_KEY}"

# Reachable without a key by design: the container HEALTHCHECK and any external
# probe have no credential to present.
_PROBES = ("/api/v1/live", "/api/v1/ready", "/api/v1/health")
_GUARDED = "/api/v1/runtime/status"


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# ── local mode: nothing changes ────────────────────────────────────────────────


async def test_no_keys_means_the_local_workflow_is_unchanged(api_client: AsyncClient) -> None:
    """The whole point of opt-in: no env var, no header, still 200."""
    response = await api_client.get(_GUARDED)

    assert response.status_code == 200, response.text


async def test_no_keys_resolves_the_anonymous_local_principal(tmp_path: Path) -> None:
    """Identity is ANONYMOUS_LOCAL — key_id 'local', role 'admin', so nothing is denied."""
    from atlas.interfaces.api.auth import ANONYMOUS_LOCAL

    async with app_client(tmp_path) as (app, client):
        assert app.state.api_keys == {}
        assert (await client.get(_GUARDED)).status_code == 200

    assert ANONYMOUS_LOCAL.key_id == "local"
    assert ANONYMOUS_LOCAL.role == "admin", "local mode must not be blocked by the readonly rule"


async def test_probes_are_open_in_local_mode(api_client: AsyncClient) -> None:
    for path in _PROBES:
        response = await api_client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"


# ── enforcing mode ─────────────────────────────────────────────────────────────


async def test_keys_set_rejects_a_request_with_no_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with app_client(tmp_path, monkeypatch, ATLAS_API_KEYS=_KEYS_ENV) as (app, client):
        assert len(app.state.api_keys) == 2, "the keys did not reach app.state"
        response = await client.get(_GUARDED)

    assert response.status_code == 401


async def test_keys_set_rejects_an_unknown_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with app_client(tmp_path, monkeypatch, ATLAS_API_KEYS=_KEYS_ENV) as (_app, client):
        response = await client.get(_GUARDED, headers=_bearer("not-a-configured-key"))

    assert response.status_code == 401


async def test_keys_set_accepts_a_valid_admin_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with app_client(tmp_path, monkeypatch, ATLAS_API_KEYS=_KEYS_ENV) as (_app, client):
        response = await client.get(_GUARDED, headers=_bearer(_ADMIN_KEY))

    assert response.status_code == 200, response.text


async def test_probes_stay_open_with_keys_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A container HEALTHCHECK has no key; making it 401 breaks orchestration."""
    async with app_client(tmp_path, monkeypatch, ATLAS_API_KEYS=_KEYS_ENV) as (_app, client):
        for path in _PROBES:
            response = await client.get(path)
            assert response.status_code == 200, f"{path} -> {response.status_code}"


# ── readonly enforcement ───────────────────────────────────────────────────────


async def test_a_readonly_key_can_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with app_client(tmp_path, monkeypatch, ATLAS_API_KEYS=_KEYS_ENV) as (_app, client):
        response = await client.get(_GUARDED, headers=_bearer(_READONLY_KEY))

    assert response.status_code == 200, response.text


async def test_a_readonly_key_cannot_mutate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented guarantee, now actually enforced."""
    async with app_client(tmp_path, monkeypatch, ATLAS_API_KEYS=_KEYS_ENV) as (_app, client):
        response = await client.post(
            "/api/v1/tasks",
            json={"request": "do something", "idempotency_key": "0123456789abcdef"},
            headers=_bearer(_READONLY_KEY),
        )

    assert response.status_code == 403
    assert response.json()["error"] == "readonly_key"


async def test_an_admin_key_can_mutate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half — the readonly rule must not block a normal key."""
    async with app_client(tmp_path, monkeypatch, ATLAS_API_KEYS=_KEYS_ENV) as (_app, client):
        response = await client.post(
            "/api/v1/tasks",
            json={"request": "do something", "idempotency_key": "fedcba9876543210"},
            headers=_bearer(_ADMIN_KEY),
        )

    assert response.status_code == 202, response.text


# ── quota identity ─────────────────────────────────────────────────────────────


async def test_the_quota_is_per_key_once_keys_are_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exhausting one key's bucket must not throttle a different key.

    Before auth was wired, ``request.state.principal`` was never set and every
    caller collapsed into one per-IP bucket — so one noisy client throttled
    everyone. Both callers here share an IP, so a pass proves the key is the
    bucket dimension.
    """
    async with app_client(
        tmp_path,
        monkeypatch,
        ATLAS_API_KEYS=_KEYS_ENV,
        ATLAS_RATE_LIMIT_CAPACITY="1",
        ATLAS_RATE_LIMIT_PER_MINUTE="0",
    ) as (_app, client):
        first = await client.get(_GUARDED, headers=_bearer(_ADMIN_KEY))
        exhausted = await client.get(_GUARDED, headers=_bearer(_ADMIN_KEY))
        other_key = await client.get(_GUARDED, headers=_bearer(_READONLY_KEY))

    assert first.status_code == 200
    assert exhausted.status_code == 429, "the admin key's bucket was not spent"
    assert other_key.status_code == 200, "the readonly key shares the admin key's bucket"


# ── failing closed ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [",,,", "ro:", " , ro: , ", "  "])
def test_configured_but_unusable_keys_refuse_to_start(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator who configured auth must never get an open server.

    ``ATLAS_API_KEYS="ro:"`` used to parse to ``{"": "readonly"}`` — non-empty, so
    the server enforced against a token nothing can send. ``",,,"`` parsed to
    ``{}`` — empty, so the server ran wide open. Both now refuse to start.
    """
    monkeypatch.setenv(_ENV_KEYS, raw)

    with pytest.raises(RuntimeError, match="no usable keys"):
        create_app()


def test_the_refusal_is_actionable_without_quoting_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal must name the variable so an operator can fix it.

    Deliberately NOT asserted here: "the message does not contain the configured
    value." That test cannot be written honestly, because every input that reaches
    this branch consists only of separators and the ``ro:`` prefix — there is no
    such thing as an unusable value that still carries key material. The real
    "never echo a presented key" property is pinned by the next test, on the 401
    path, where a genuine secret IS in play.
    """
    monkeypatch.setenv(_ENV_KEYS, ",,,")

    with pytest.raises(RuntimeError) as excinfo:
        create_app()

    assert _ENV_KEYS in str(excinfo.value), "the operator cannot tell which setting is wrong"


async def test_a_rejected_key_is_never_echoed_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 must not reflect the credential the caller presented.

    An error message built as f"invalid API key: {token}" is an easy and common
    mistake, and it lands the secret in the browser devtools, in any proxy access
    log, and in whatever the client logs on failure. The presented value must
    appear in neither the body nor the headers.
    """
    presented = "wrong-key-supersecretvalue"

    async with app_client(tmp_path, monkeypatch, ATLAS_API_KEYS=_KEYS_ENV) as (_app, client):
        response = await client.get(_GUARDED, headers=_bearer(presented))

    assert response.status_code == 401
    assert presented not in response.text
    assert presented not in str(dict(response.headers))
    assert "supersecretvalue" not in response.text, "a fragment of the key leaked"


def test_an_absent_env_var_starts_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_ENV_KEYS, raising=False)

    assert create_app().state.api_keys == {}


# ── parsing ────────────────────────────────────────────────────────────────────


def test_parse_api_keys_assigns_roles_by_prefix() -> None:
    parsed = parse_api_keys(f"{_ADMIN_KEY},ro:{_READONLY_KEY}")

    assert parsed[_ADMIN_KEY] == "admin"
    assert parsed[_READONLY_KEY] == "readonly"


def test_parse_api_keys_drops_empty_key_material() -> None:
    """ "ro:" is a prefix with no key — storing it would enforce against nothing."""
    assert parse_api_keys("ro:") == {}
    assert parse_api_keys(",,,") == {}
    assert parse_api_keys("") == {}
    assert parse_api_keys(None) == {}
