"""Offline end-to-end block test for v3.1 planning, runtime, store and coordinator."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import asyncio
import pytest

from paper_eval.apc_aligned_baseline import APC_BASELINE_HISTORIES, build_apc_aligned_baseline_plan
from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.baseline_acceptance import ACCEPTANCE_SCHEMA, EXPECTED_BASELINE_RUN_ID
from paper_eval.membind_v31 import (
    CertificationRecord,
    DependencyClass,
    EffectClass,
    OperatorContract,
    StateCutCertification,
)
from paper_eval.membind_v31.live_block import (
    MemBindV31LiveBlockError,
    V31LiveHooks,
    _invoke_runtime_builder,
    execute_v31_live_block,
)
from paper_eval.membind_v31.method_plan import build_membind_v31_method_plan
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact


@dataclass(frozen=True)
class _Episode:
    source_sequence: int
    source_hash: str
    reference_time: str
    body: str
    group_id: str = "original"

    @property
    def name(self) -> str:
        return f"history::episode::{self.source_sequence:04d}"


def _plan() -> dict[str, object]:
    baseline = build_apc_aligned_baseline_plan(
        run_id=EXPECTED_BASELINE_RUN_ID,
        history_source_sha256s={
            history: [f"{index + 1:064x}"]
            for index, history in enumerate(APC_BASELINE_HISTORIES)
        },
        interarrival_ns=10,
        execution_envelope_sha256="a" * 64,
        service_reference_ns=12,
        normalized_offered_load=1.2,
    )
    acceptance = {
        "schema_version": ACCEPTANCE_SCHEMA,
        "status": "PASS",
        "artifact_status": "SEALED_VALID",
        "semantic_verdicts": {
            method: {"direct_violations": 0, "semantic_status": "SAFE"}
            for method in ("U0-aligned", "A0-aligned", "P(C=2)-aligned")
        },
        "run_id": EXPECTED_BASELINE_RUN_ID,
        "completed_block_count": 12,
        "terminal_episode_count_per_method": 188,
        "plan_payload_sha256": baseline["payload_sha256"],
        "source_manifest_sha256": baseline["source_manifest_sha256"],
        "arrival_trace_sha256": baseline["arrival_trace_sha256"],
        "shared_execution_envelope_sha256": baseline["shared_execution_envelope_sha256"],
        "global_llm_admission_k": 2,
        "execution_identity_sha256": "b" * 64,
        "block_result_payload_sha256s": [f"{100 + index:064x}" for index in range(12)],
        "quality_run_id": "quality-test",
        "quality_report_payload_sha256": "c" * 64,
        "quality_identity_sha256": "d" * 64,
        "quality_runtime_identity_sha256": "e" * 64,
    }
    acceptance["payload_sha256"] = payload_sha256(acceptance)
    return build_membind_v31_method_plan(
        run_id="membind-v31-live-test",
        verified_baseline_plan=baseline,
        verified_baseline_acceptance=acceptance,
        methodology_sha256="f" * 64,
        workplan_sha256="1" * 64,
    )


def _certification() -> StateCutCertification:
    records = []
    for index, operator_name in enumerate(
        ("graphiti.extract_nodes", "graphiti.extract_edges"), start=1
    ):
        records.append(
            CertificationRecord.create(
                operator_contract=OperatorContract.create(
                    operator_name=operator_name,
                    dependency_class=DependencyClass.EVIDENCE_BOUND,
                    effect_class=EffectClass.PURE,
                ),
                memory_backend_identity_sha256="a" * 64,
                adapter_identity_sha256="b" * 64,
                operator_identity_sha256=f"{index:064x}",
                code_revision_sha256="c" * 64,
                prompt_identity_sha256=f"{index + 10:064x}",
                schema_identity_sha256="d" * 64,
                config_identity_sha256="e" * 64,
                allowed_evidence_inputs=("current_source", "evidence_snapshot"),
                allowed_upstream_outputs=(),
                allowed_apis=("llm.generate_response",),
                forbidden_apis=("memory.search", "memory.write"),
                qualification_trace_sha256=f"{index + 20:064x}",
                persistent_state_read_count=0,
                persistent_state_write_count=0,
                undeclared_external_side_effect_count=0,
                future_evidence_access_count=0,
                undeclared_state_facing_call_count=0,
            )
        )
    return StateCutCertification.create(records)


class _RequestClient:
    def observation(self):
        return {"configured_limit": 2, "observed_max_inflight": 0, "policy": "BARRIER"}

    @asynccontextmanager
    async def frontier_bind_region(self, _stream_id, _sequence):
        yield


def test_runtime_builder_response_hook_preserves_old_explicit_injection_signature() -> None:
    calls: list[dict[str, object]] = []

    def old_builder(*, env, policy, request_id_prefix, observer):
        calls.append(
            {
                "env": env,
                "policy": policy,
                "request_id_prefix": request_id_prefix,
                "observer": observer,
            }
        )
        return "legacy-runtime"

    response_observer = lambda _row: None
    selected = _invoke_runtime_builder(
        old_builder,
        response_observer=response_observer,
        env={"SAFE": "value"},
        policy="FIFO",
        request_id_prefix="legacy-hook",
        observer=lambda _row: None,
    )

    assert selected == "legacy-runtime"
    assert len(calls) == 1
    assert "response_observer" not in calls[0]


def test_live_block_binds_plan_runtime_source_and_durable_publication(tmp_path: Path) -> None:
    state: dict[str, object] = {"visible": [], "closed": False}
    certification = _certification()
    certification_sha = certification.certification_sha256

    class Adapter:
        async def prepare(self, compile_input):
            return PreparedArtifact.create(
                source_sequence=compile_input.source.source_sequence,
                source_sha256=compile_input.source.source_sha256,
                evidence_sha256=compile_input.evidence.evidence_prefix_sha256,
                certification_sha256=certification_sha,
                raw_nodes=[],
                raw_edges=[],
                pure_intermediates={"node_episode_index_map": {}},
            )

        async def bind(self, compile_input, artifact, *, logical_time_ns: int):
            state["visible"].append(compile_input.source.episode_projection["name"])
            return {"source_sequence": artifact.source_sequence}

    runtime = SimpleNamespace(
        graphiti=SimpleNamespace(),
        admitted_llm=_RequestClient(),
        shared_execution_envelope_sha256="a" * 64,
        method_execution_identity_sha256="4" * 64,
        method_public_identity={"request_admission": {"policy": "BARRIER"}},
    )

    async def namespace_probe(_runtime, _namespace):
        return {
            "node_count": len(state["visible"]),
            "relationship_count": 0,
            "episode_names": list(state["visible"]),
        }

    runtime_builder_kwargs: dict[str, object] = {}

    def runtime_builder(**kwargs: object):
        runtime_builder_kwargs.update(kwargs)
        response_observer = kwargs["response_observer"]
        assert callable(response_observer)
        response_observer(
            {
                "schema_version": "membind.paper-eval-v3.transport-response.v1",
                "event_type": "llm_transport_response",
                "transport_attempt_index": 0,
                "retry_index": None,
                "request_kind": "FRONTIER",
                "stream_id": "07741c45",
                "source_sequence": 0,
                "requested_max_tokens": 16_384,
                "effective_max_tokens": 16_384,
                "response_format_sha256": "a" * 64,
                "json_schema_sha256": "b" * 64,
                "response_byte_length": 7,
                "response_sha256": "c" * 64,
                "finish_reason": "stop",
                "prompt_tokens": 25_243,
                "completion_tokens": 7,
                "total_tokens": 25_250,
                "structured_backend_identity": "xgrammar",
            }
        )
        return runtime

    hooks = V31LiveHooks(
        runtime_builder=runtime_builder,
        runtime_ready=lambda _runtime: asyncio.sleep(0),
        namespace_probe=namespace_probe,
        namespace_episode=lambda episode, namespace: replace(episode, group_id=namespace),
        source_visibility_probe=lambda _runtime, source: asyncio.sleep(
            0, result=source.episode_projection["name"] in state["visible"]
        ),
        reference_time_to_ns=lambda _value: 1,
        adapter_factory=lambda _runtime, _certification: Adapter(),
        close_runtime=lambda _runtime: asyncio.sleep(0, result=state.update(closed=True)),
    )
    result = asyncio.run(
        execute_v31_live_block(
            verified_plan=_plan(),
            block_index=0,
            episodes=(
                _Episode(0, f"{1:064x}", "2026-01-01T00:00:00+00:00", "private"),
            ),
            env={},
            block_root=tmp_path / "block-00",
            state_cut_certification=certification,
            compile_workers=2,
            lookahead=2,
            hooks=hooks,
        )
    )

    assert result["status"] == "PASS"
    assert result["source_count"] == 1
    assert result["direct_violation_count"] == 0
    assert result["checkpoint"]["complete_coverage"] is True
    assert result["final_namespace"]["episode_names"] == ["history::episode::0000"]
    assert state["closed"] is True
    assert (tmp_path / "block-00/result.json").is_file()
    llm_rows = (tmp_path / "block-00/llm.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(llm_rows) == 1
    assert "llm_transport_response" in llm_rows[0]
    assert "private" not in llm_rows[0]
    assert callable(runtime_builder_kwargs["observer"])
    assert "private" not in (tmp_path / "block-00/events.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("compile_workers", "lookahead"),
    ((3, 2), (2, 3)),
)
def test_live_block_rejects_runtime_knob_override_before_io(
    tmp_path: Path, compile_workers: int, lookahead: int
) -> None:
    with pytest.raises(MemBindV31LiveBlockError, match="runtime_knob_plan_mismatch"):
        asyncio.run(
            execute_v31_live_block(
                verified_plan=_plan(),
                block_index=0,
                episodes=(),
                env={},
                block_root=tmp_path / "must-not-exist",
                state_cut_certification=_certification(),
                compile_workers=compile_workers,
                lookahead=lookahead,
            )
        )
    assert not (tmp_path / "must-not-exist").exists()
