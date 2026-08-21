from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.block_lifecycle import (
    BlockLifecycle,
    LifecycleError,
)
from saturated_fixed_work_baseline_v1_3.backend_contract import load_frozen_contracts
from saturated_fixed_work_baseline_v1_3.native_serial_certification import (
    certify_native_serial_fixture,
)
from saturated_fixed_work_baseline_v1_3.real_seam_observer import (
    observe_call,
    wrap_seam,
)


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "saturated_fixed_work_baseline_v1_3"


def test_protocol_has_no_resource_envelope_gate() -> None:
    text = "\n".join(
        (PACKAGE / name).read_text(encoding="utf-8")
        for name in ("PROTOCOL.md", "README.md", "TEST_QUALIFICATION_GATE.md")
    )
    forbidden = (
        "RESOURCE_ENVELOPE_ID",
        "resource_gate",
        "gpu_uuid",
        "production_sampler",
        "resource_evidence",
        "historical/current resource parity",
    )
    assert not any(token.lower() in text.lower() for token in forbidden)


def test_active_campaign_imports_no_resource_evidence() -> None:
    source = (PACKAGE / "src/saturated_fixed_work_baseline_v1_3/simple_campaign.py").read_text(
        encoding="utf-8"
    )
    assert not re.search(r"(?:resource_evidence|production_sampler)", source)


def test_block_validity_does_not_depend_on_resource_identity() -> None:
    source = (PACKAGE / "src/saturated_fixed_work_baseline_v1_3/simple_campaign.py").read_text(
        encoding="utf-8"
    )
    assert "gpu_uuid" not in source
    assert "resource_envelope_id" not in source


def test_frozen_contracts_are_shared_by_b0_and_b1() -> None:
    backend, client = load_frozen_contracts(PACKAGE / "configs")
    assert backend["construction"]["port"] == 8000
    assert backend["embedding"]["port"] == 8001
    assert backend["construction"]["max_num_seqs"] == "vLLM pinned-version default"
    assert backend["construction"]["max_num_batched_tokens"] == "vLLM pinned-version default"
    assert client["methods"] == {
        "B0_NATIVE_SERIAL": client["common"],
        "B1_NAIVE_WHOLE_UPDATE_ASYNC": client["common"],
    }
    assert client["common"]["llm"] == {
        "temperature": 0,
        "top_p": 1.0,
        "max_tokens": 16384,
        "seed": 20260806,
        "structured_output_request_mode": "json_schema",
        "default_chat_template_kwargs": {"enable_thinking": False},
    }
    assert client["method_overrides"] == {}


def test_lifecycle_timer_excludes_warmup_and_validation() -> None:
    clock = iter(range(100, 110))
    lifecycle = BlockLifecycle(monotonic_ns=lambda: next(clock))
    lifecycle.fresh_namespace()
    lifecycle.backend_prepared()
    lifecycle.service_ready()
    lifecycle.warmup_complete()
    lifecycle.backend_idle()
    lifecycle.formal_start()
    lifecycle.construction_complete()
    lifecycle.durable_complete()
    lifecycle.validation_complete()
    assert lifecycle.timer_start_ns == 105
    assert lifecycle.timer_stop_ns == 107
    assert lifecycle.build_makespan_ns == 2


def test_lifecycle_rejects_validation_before_durable_completion() -> None:
    lifecycle = BlockLifecycle(monotonic_ns=lambda: 1)
    lifecycle.fresh_namespace()
    lifecycle.backend_prepared()
    lifecycle.service_ready()
    lifecycle.warmup_complete()
    lifecycle.backend_idle()
    lifecycle.formal_start()
    with pytest.raises(LifecycleError, match="DURABLE_COMPLETION_REQUIRED"):
        lifecycle.validation_complete()


def test_native_serial_certification_compares_operator_lineage_and_effects() -> None:
    fixture = [
        {"source_sequence": 0, "operators": ["extract", "resolve", "persist", "publish"], "effect": "a"},
        {"source_sequence": 1, "operators": ["extract", "resolve", "persist", "publish"], "effect": "b"},
    ]
    result = certify_native_serial_fixture(fixture, fixture)
    assert result["status"] == "PASS"
    assert result["checks"]["source_coverage_equal"] is True
    assert result["checks"]["operator_lineage_equal"] is True
    assert result["checks"]["effect_cardinality_equal"] is True
    assert result["checks"]["publication_order_equal"] is True


def test_real_seam_observer_preserves_call_and_return_without_side_effects() -> None:
    seen: list[dict] = []

    def seam(payload: dict, *, batch: tuple[int, ...]) -> dict:
        return {"payload": payload, "batch": batch}

    result = observe_call(
        seam,
        {"candidate_id": "n-1"},
        batch=(0, 1),
        observer=lambda record: seen.append(record),
    )
    assert result == {"payload": {"candidate_id": "n-1"}, "batch": (0, 1)}
    assert seen[0]["args"] == ({"candidate_id": "n-1"},)
    assert seen[0]["kwargs"] == {"batch": (0, 1)}
    assert seen[0]["provider_calls"] == 0
    assert seen[0]["db_io"] == 0
    assert seen[0]["input_semantic_hash"] == "NOT_OBSERVABLE"
    assert seen[0]["batch_membership_hash"] == "NOT_OBSERVABLE"


def test_real_seam_observer_supports_captured_graphiti_async_shape() -> None:
    from paper_eval.s5_graphiti_semantic_binding import S5GraphitiSemanticBinding

    async def extract_nodes(
        clients: object,
        episode: object,
        previous: list[object],
        *_args: object,
    ) -> tuple[list[dict[str, str]], dict[str, int]]:
        return [{"candidate_id": "n-1"}], {"n-1": 0}

    binding = S5GraphitiSemanticBinding(
        extract_nodes=extract_nodes,
        resolve_extracted_nodes=extract_nodes,
        extract_attributes_from_nodes=extract_nodes,
        extract_edges=extract_nodes,
        resolve_extracted_edges=extract_nodes,
        resolve_edge_pointers=extract_nodes,
        process_episode_data=extract_nodes,
    )
    seen: list[dict] = []
    observed = wrap_seam(binding.extract_nodes, seen.append)
    result = asyncio.run(observed("clients", "episode", [], "schema"))
    assert result == ([{"candidate_id": "n-1"}], {"n-1": 0})
    assert len(seen) == 1
    assert seen[0]["provider_calls"] == 0
    assert seen[0]["db_io"] == 0


def test_native_certification_payload_is_json_serializable() -> None:
    result = certify_native_serial_fixture([], [])
    json.dumps(result, sort_keys=True)
