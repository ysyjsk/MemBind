from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from saturated_fixed_work_baseline_v1_3.membind_v7.development_provider_diagnostics import (
    DevelopmentSchemaOutputError,
    augment_development_failure,
    install_development_schema_diagnostics,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.engineering_observer_runtime import (
    CompositeEngineeringError,
)


class _Expected(BaseModel):
    count: int


class _InvalidValidatedClient:
    async def generate_response(self, *_args: object, **_kwargs: object) -> object:
        try:
            _Expected.model_validate({"count": "private-invalid-value"})
        except Exception as cause:
            raise CompositeEngineeringError(
                "Graphiti response failed Pydantic validation"
            ) from cause
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_schema_diagnostic_retains_only_location_type_and_request_scope() -> None:
    client = _InvalidValidatedClient()
    install_development_schema_diagnostics(
        client,
        scope_reader=lambda: {
            "phase": "OLD",
            "source_sequence": 1,
            "state_version": 0,
            "request_ordinal": 3,
            "prompt_name": "resolve_nodes.resolve_nodes",
            "private_prompt": "must not persist",
        },
    )

    with pytest.raises(DevelopmentSchemaOutputError) as captured:
        await client.generate_response([], response_model=_Expected)

    diagnostic = captured.value.diagnostic
    assert diagnostic["prompt_name"] == "resolve_nodes.resolve_nodes"
    assert diagnostic["response_model"] == f"{_Expected.__module__}.{_Expected.__qualname__}"
    assert diagnostic["validation_error_count"] == 1
    assert diagnostic["validation_errors"] == [
        {"location": ["count"], "type": "int_parsing"}
    ]
    assert diagnostic["validation_input_persisted"] is False
    assert "private-invalid-value" not in json.dumps(diagnostic, sort_keys=True)
    assert "must not persist" not in json.dumps(diagnostic, sort_keys=True)


def test_schema_diagnostic_augments_sanitized_failure_without_raw_output() -> None:
    error = DevelopmentSchemaOutputError(
        {
            "schema_version": "membind.v7.development-schema-diagnostic.v1",
            "prompt_name": "extract_edges.edge",
            "response_model": "graphiti_core.prompts.extract_edges.ExtractedEdges",
            "validation_error_count": 1,
            "validation_errors": [{"location": ["edges", 0], "type": "missing"}],
            "validation_input_persisted": False,
            "raw_request_persisted": False,
            "raw_response_persisted": False,
        }
    )
    failure = {
        "status": "FAILED_CLOSED",
        "failure_class": "OBSERVER_RUNTIME_FAILURE",
        "gate_outcome": "NOT_EVALUATED",
    }

    augmented = augment_development_failure(failure, error)

    assert augmented["failure_class"] == (
        "INFRASTRUCTURE_PROVIDER_STRUCTURED_OUTPUT_INVALID"
    )
    assert augmented["provider_schema_diagnostic"] == error.diagnostic
    assert augmented["gate_outcome"] == "NOT_EVALUATED"
    assert augmented["raw_response_persisted"] is False


@pytest.mark.asyncio
async def test_non_schema_composite_error_is_not_reclassified() -> None:
    class Client:
        async def generate_response(self, *_args: object, **_kwargs: object) -> object:
            raise CompositeEngineeringError("SiliconFlow embedding dimension mismatch")

    client = Client()
    install_development_schema_diagnostics(client, scope_reader=lambda: None)
    with pytest.raises(CompositeEngineeringError, match="dimension"):
        await client.generate_response([], response_model=_Expected)


def test_diagnostic_installation_rejects_double_wrap() -> None:
    client = SimpleNamespace(generate_response=lambda: None)
    install_development_schema_diagnostics(client, scope_reader=lambda: None)
    with pytest.raises(ValueError, match="already installed"):
        install_development_schema_diagnostics(client, scope_reader=lambda: None)
