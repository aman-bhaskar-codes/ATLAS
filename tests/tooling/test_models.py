"""Foundation unit tests — universal models, identity, policy, errors (§45)."""

from __future__ import annotations

import datetime

import pytest
from pydantic import ValidationError

from atlas.capabilities.domain.common import Provenance, SourceKind
from atlas.infra.errors import AtlasError
from atlas.infra.ids import CorrelationId
from atlas.infra.types import CostClass, Tier, ToolResult
from atlas.tooling.compat import to_tool_call_spec
from atlas.tooling.errors import (
    ToolAlreadyRegistered,
    ToolDisabled,
    ToolingError,
    ToolNotFound,
)
from atlas.tooling.models.identity import ToolNamespace, build_tool_id, parse_tool_id
from atlas.tooling.models.tool_definition import ExecutionType, Locality, UniversalToolDefinition
from atlas.tooling.models.tool_invocation import UniversalToolInvocation
from atlas.tooling.models.tool_policy import ToolPolicyMetadata
from atlas.tooling.models.tool_provenance import ToolProvenance
from atlas.tooling.models.tool_result import FailureKind, UniversalToolResult
from atlas.tooling.models.tool_schema import ToolInputSchema


def _definition(**overrides: object) -> UniversalToolDefinition:
    defaults: dict[str, object] = {
        "id": build_tool_id(ToolNamespace.NATIVE, "atlas", "filesystem"),
        "name": "filesystem",
        "namespace": ToolNamespace.NATIVE,
        "provider": "atlas",
        "description": "Filesystem tool",
        "operations": ("read", "write"),
        "input_schema": ToolInputSchema.from_operations(("read", "write")),
        "execution_type": ExecutionType.NATIVE,
        "adapter": "native",
        "provenance": ToolProvenance(source_kind=SourceKind.LOCAL, provider="atlas"),
        "locality": Locality.LOCAL,
        "safety_tool": "filesystem",
    }
    defaults.update(overrides)
    return UniversalToolDefinition(**defaults)  # type: ignore[arg-type]


# ── Identity (§7) ──────────────────────────────────────────────────────── #


def test_tool_identity_is_deterministic_and_parseable() -> None:
    first = build_tool_id(ToolNamespace.NATIVE, "atlas", "filesystem")
    second = build_tool_id("native", "atlas", "filesystem")
    assert first == second == "native:atlas:filesystem"
    assert parse_tool_id(first) == ("native", "atlas", "filesystem")


def test_tool_identity_supports_dotted_names() -> None:
    tool_id = build_tool_id(ToolNamespace.CAPABILITY, "atlas", "knowledge.search")
    assert tool_id == "capability:atlas:knowledge.search"
    assert parse_tool_id(tool_id) == ("capability", "atlas", "knowledge.search")


@pytest.mark.parametrize(
    ("namespace", "provider", "name"),
    [
        ("native", "", "x"),
        ("native", "Atlas", "x"),  # uppercase rejected: canonical form is lowercase
        ("native", "a:b", "x"),  # separator smuggling rejected
        ("", "atlas", "x"),
        ("native", "atlas", ""),
    ],
)
def test_tool_identity_rejects_ambiguous_components(namespace: str, provider: str, name: str) -> None:
    with pytest.raises(ValueError):
        build_tool_id(namespace, provider, name)


def test_parse_tool_id_rejects_malformed_ids() -> None:
    with pytest.raises(ValueError):
        parse_tool_id("native:filesystem")  # missing provider
    with pytest.raises(ValueError):
        parse_tool_id("just-a-name")


# ── Definition (§6/§42/§58) ────────────────────────────────────────────── #


def test_definition_rejects_id_that_does_not_match_identity() -> None:
    with pytest.raises(ValidationError, match="canonical"):
        _definition(id="native:atlas:shell")


def test_definition_rejects_empty_operations() -> None:
    with pytest.raises(ValidationError, match="operation"):
        _definition(operations=())


def test_definition_is_frozen() -> None:
    definition = _definition()
    with pytest.raises(ValidationError):
        definition.name = "renamed"  # type: ignore[misc]


def test_definition_policy_views_delegate_to_policy_metadata() -> None:
    definition = _definition(
        policy=ToolPolicyMetadata(
            default_tier=Tier.CONFIRM,
            side_effects=True,
            requires_auth=True,
            cost_class=CostClass.FREE_QUOTA,
        )
    )
    assert definition.default_tier == Tier.CONFIRM
    assert definition.side_effects is True
    assert definition.requires_auth is True
    assert definition.cost_class == CostClass.FREE_QUOTA


def test_definition_serialization_round_trip_is_lossless() -> None:
    definition = _definition(
        policy=ToolPolicyMetadata(default_tier=Tier.NOTIFY, side_effects=True),
        provenance=ToolProvenance(
            source_kind=SourceKind.WEB,
            provider="atlas",
            retrieved_ts=datetime.datetime(2026, 9, 24, 12, 0, tzinfo=datetime.UTC),
        ),
        tags=("core",),
    )
    restored = UniversalToolDefinition.model_validate_json(definition.model_dump_json())
    assert restored == definition


def test_definition_version_defaults_and_survives_round_trip() -> None:
    definition = _definition(definition_version=2)
    restored = UniversalToolDefinition.model_validate_json(definition.model_dump_json())
    assert restored.definition_version == 2


# ── Schema (§30) ───────────────────────────────────────────────────────── #


def test_input_schema_derives_operation_vocabulary_not_bare_object() -> None:
    schema = ToolInputSchema.from_operations(("read", "write", "delete"))
    operation_property = schema.fallback["properties"]["operation"]
    assert operation_property["enum"] == ["read", "write", "delete"]
    assert schema.schema_for("read") == schema.fallback
    assert schema.operations == {}


def test_input_schema_prefers_per_operation_schema_when_present() -> None:
    schema = ToolInputSchema(
        operations={"read": {"type": "object", "properties": {"path": {"type": "string"}}}},
    )
    assert schema.schema_for("read")["properties"]["path"] == {"type": "string"}
    assert schema.schema_for("unknown-op") == schema.fallback


# ── Invocation (§9) ────────────────────────────────────────────────────── #


def test_invocation_defaults_and_freezing() -> None:
    invocation = UniversalToolInvocation(
        tool_id="native:atlas:shell",
        operation="read_only",
        arguments={"command": ["ls"]},
        correlation_id=CorrelationId("c-1"),
    )
    assert invocation.source.value == "system"
    assert invocation.timeout_s is None
    with pytest.raises(ValidationError):
        invocation.operation = "side_effect"  # type: ignore[misc]


def test_invocation_rejects_malformed_tool_id_and_blank_operation() -> None:
    with pytest.raises(ValidationError):
        UniversalToolInvocation(
            tool_id="not-an-identity",
            operation="read",
            correlation_id=CorrelationId("c-1"),
        )
    with pytest.raises(ValidationError):
        UniversalToolInvocation(
            tool_id="native:atlas:shell",
            operation="  ",
            correlation_id=CorrelationId("c-1"),
        )


# ── Result (§10) ───────────────────────────────────────────────────────── #


def test_result_failure_helper_is_structured() -> None:
    result = UniversalToolResult.failure(
        tool_id="native:atlas:shell",
        kind=FailureKind.POLICY_DENIED,
        message="denied (tier BLOCK): nope",
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "tooling.policy_denied"
    assert result.error.retryable is False


def test_result_preserves_side_effects_and_provenance() -> None:
    from atlas.infra.types import SideEffect

    result = UniversalToolResult(
        ok=True,
        tool_id="native:atlas:filesystem",
        data="bytes",
        side_effects=(SideEffect(kind="write", target="/tmp/x"),),
        provenance=(ToolProvenance(source_kind=SourceKind.LOCAL, provider="atlas"),),
    )
    assert result.side_effects[0].kind == "write"
    assert result.provenance[0].to_domain().source_kind == SourceKind.LOCAL


def test_provenance_converts_losslessly_to_domain_model() -> None:
    tool_provenance = ToolProvenance(source_kind=SourceKind.MCP, provider="mcp:github")
    domain = tool_provenance.to_domain()
    assert isinstance(domain, Provenance)
    assert ToolProvenance.from_domain(domain) == tool_provenance


def test_result_from_tool_result_shape() -> None:
    """The infra ToolResult (native funnel output) normalizes losslessly."""
    native = ToolResult(
        ok=True,
        output={"listing": ["a", "b"]},
        side_effects=(),
        duration_ms=12,
    )
    universal = UniversalToolResult(
        ok=native.ok,
        tool_id="native:atlas:filesystem",
        data=native.output,
        duration_ms=native.duration_ms,
    )
    assert universal.ok and universal.data == {"listing": ["a", "b"]} and universal.duration_ms == 12


# ── Errors (§40) ───────────────────────────────────────────────────────── #


def test_tooling_errors_root_at_atlas_error() -> None:
    for exc_type in (ToolingError, ToolAlreadyRegistered, ToolNotFound, ToolDisabled):
        assert issubclass(exc_type, AtlasError)
    assert ToolNotFound("x").code == "tooling.not_found"
    assert ToolDisabled("x").code == "tooling.disabled"
    assert ToolAlreadyRegistered("x").code == "tooling.already_registered"


# ── Compat (§60) ───────────────────────────────────────────────────────── #


def test_to_tool_call_spec_uses_universal_metadata() -> None:
    spec = to_tool_call_spec(_definition(operations=("read", "write")))
    assert spec.name == "native:atlas:filesystem"
    assert "filesystem tool" in spec.description.lower()
    operation_property = spec.parameters["properties"]["operation"]  # type: ignore[union-attr,index]
    assert operation_property["enum"] == ["read", "write"]  # type: ignore[index]
    assert spec.parameters["required"] == ["operation"]  # type: ignore[union-attr,index]
