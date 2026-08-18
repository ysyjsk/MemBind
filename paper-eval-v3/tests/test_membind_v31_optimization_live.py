"""Offline end-to-end tests for the non-mergeable W=4 pilot executor."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.apc_aligned_baseline import build_apc_aligned_baseline_plan
from paper_eval.membind_v31 import (
    CertificationRecord,
    DependencyClass,
    EffectClass,
    OperatorContract,
    StateCutCertification,
)
from paper_eval.membind_v31.baseline_acceptance import EXPECTED_BASELINE_RUN_ID
from paper_eval.membind_v31.live_block import V31LiveHooks
from paper_eval.membind_v31.method_plan import build_membind_v31_live_plan
from paper_eval.membind_v31.optimization_live import (
    OptimizationPilotExecutionError,
    execute_w4_pilot,
)
from paper_eval.membind_v31.optimization_pilot import (
    build_w4_pilot_contract,
    derive_w4_pilot_cache_salt,
    derive_w4_pilot_namespace,
)
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact


HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
RUN_ID = "membind-v31-opt-w4-20260818-live-test"


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


def _formal_plan() -> dict[str, object]:
    sources = {
        history: [f"{position * 1000 + sequence + 1:064x}" for sequence in range(15)]
        for position, history in enumerate(HISTORIES)
    }
    baseline = build_apc_aligned_baseline_plan(
        run_id=EXPECTED_BASELINE_RUN_ID,
        history_source_sha256s=sources,
        interarrival_ns=1,
        execution_envelope_sha256="a" * 64,
        service_reference_ns=2,
        normalized_offered_load=2.0,
    )
    return build_membind_v31_live_plan(
        run_id="membind-v31-live-optimization-test",
        verified_baseline_plan=baseline,
        methodology_sha256="b" * 64,
        workplan_sha256="c" * 64,
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
    def __init__(self, observer) -> None:
        self._observer = observer
        self._sequence = 0
        self._emit("INITIAL")

    def _emit(self, reason: str) -> None:
        self._observer(
            {
                "schema_version": "membind.paper-eval-v3.membind-v31-admission-state.v1",
                "event_type": "admission_snapshot",
                "event_sequence": self._sequence,
                "reason": reason,
                "timestamp_ns": time.monotonic_ns(),
                "configured_limit": 2,
                "active_count": 0,
                "waiting_count": 0,
                "active_compile_count": 0,
                "active_frontier_count": 0,
                "waiting_compile_count": 0,
                "waiting_frontier_count": 0,
                "frontier_bind_region_count": 0,
                "barrier_holds": False,
                "policy": "CACHE_AFFINE",
            }
        )
        self._sequence += 1

    @asynccontextmanager
    async def frontier_bind_region(self, _stream_id: str, _sequence: int):
        self._emit("FRONTIER_START")
        yield
        self._emit("FRONTIER_END")

    def observation(self) -> dict[str, int]:
        return {
            "configured_limit": 2,
            "active_count": 0,
            "waiting_count": 0,
            "observed_max_inflight": 0,
        }


def _fixture(tmp_path: Path, *, fail_sequence: int | None = None):
    formal = _formal_plan()
    namespace = derive_w4_pilot_namespace(RUN_ID)
    cache_salt = derive_w4_pilot_cache_salt(
        pilot_run_id=RUN_ID,
        namespace=namespace,
        parent_formal_plan_payload_sha256=str(formal["payload_sha256"]),
    )
    root = tmp_path / "pilot"
    contract = build_w4_pilot_contract(
        verified_formal_plan=formal,
        pilot_run_id=RUN_ID,
        attempt_id=f"{RUN_ID}-attempt-001",
        namespace=namespace,
        cache_salt_sha256=cache_salt,
        output_root=root,
        compile_workers=2,
        lookahead=4,
        bind_workers=1,
        global_llm_admission_k=2,
    )
    sources = formal["history_source_sha256s"]["07741c45"][:12]
    episodes = tuple(
        _Episode(index, str(source_hash), str(index + 1), "private")
        for index, source_hash in enumerate(sources)
    )
    certification = _certification()
    state: dict[str, object] = {"visible": [], "closed": False}

    class Adapter:
        async def prepare(self, compile_input):
            sequence = compile_input.source.source_sequence
            if sequence == fail_sequence:
                raise RuntimeError("private model response")
            return PreparedArtifact.create(
                source_sequence=sequence,
                source_sha256=compile_input.source.source_sha256,
                evidence_sha256=compile_input.evidence.evidence_prefix_sha256,
                certification_sha256=certification.certification_sha256,
                raw_nodes=[],
                raw_edges=[],
                pure_intermediates={"node_episode_index_map": {}},
            )

        async def bind(self, compile_input, artifact, *, logical_time_ns: int):
            assert logical_time_ns >= 0
            state["visible"].append(compile_input.source.episode_projection["name"])
            return {"source_sequence": artifact.source_sequence}

    runtime_box: dict[str, object] = {}

    def runtime_builder(**kwargs):
        client = _RequestClient(kwargs["admission_observer"])
        runtime = SimpleNamespace(
            admitted_llm=client,
            shared_execution_envelope_sha256="a" * 64,
            method_execution_identity_sha256="d" * 64,
        )
        runtime_box["runtime"] = runtime
        return runtime

    async def namespace_probe(_runtime, _namespace):
        visible = list(state["visible"])
        return {
            "node_count": len(visible),
            "relationship_count": 0,
            "episode_names": visible,
        }

    hooks = V31LiveHooks(
        runtime_builder=runtime_builder,
        runtime_ready=lambda _runtime: asyncio.sleep(0),
        namespace_probe=namespace_probe,
        namespace_episode=lambda episode, selected: replace(episode, group_id=selected),
        source_visibility_probe=lambda _runtime, source: asyncio.sleep(
            0,
            result=source.episode_projection["name"] in state["visible"],
        ),
        reference_time_to_ns=lambda value: int(value),
        adapter_factory=lambda _runtime, _certification: Adapter(),
        close_runtime=lambda _runtime: asyncio.sleep(0, result=state.update(closed=True)),
    )
    return formal, contract, episodes, certification, hooks, root, state


def test_w4_pilot_executor_persists_complete_non_mergeable_evidence(tmp_path: Path) -> None:
    formal, contract, episodes, certification, hooks, root, state = _fixture(tmp_path)

    result = asyncio.run(
        execute_w4_pilot(
            contract=contract,
            verified_formal_plan=formal,
            episodes=episodes,
            env={},
            output_root=root,
            state_cut_certification=certification,
            implementation_sha256="e" * 64,
            hooks=hooks,
        )
    )

    assert result["status"] == "PASS"
    assert result["formal_main_table_eligible"] is False
    assert result["publication_source_sequences"] == list(range(12))
    assert result["queue_diagnostic"]["scheduler"]["ready_work_observable"] is True
    assert state["closed"] is True
    assert (root / "PILOT_CONTRACT.json").is_file()
    assert (root / "manifest.json").is_file()
    assert (root / "checkpoint.json").is_file()
    assert (root / "events.jsonl").is_file()
    assert (root / "queue.jsonl").is_file()
    assert (root / "QUEUE_DIAGNOSTIC.json").is_file()
    assert len(list((root / "private/prepared").glob("*.json"))) == 12
    assert "private" not in (root / "events.jsonl").read_text(encoding="utf-8")


def test_w4_pilot_executor_checkpoints_failure_and_never_repairs(tmp_path: Path) -> None:
    formal, contract, episodes, certification, hooks, root, state = _fixture(
        tmp_path, fail_sequence=1
    )

    with pytest.raises(Exception, match="compile_failed"):
        asyncio.run(
            execute_w4_pilot(
                contract=contract,
                verified_formal_plan=formal,
                episodes=episodes,
                env={},
                output_root=root,
                state_cut_certification=certification,
                implementation_sha256="e" * 64,
                hooks=hooks,
            )
        )

    failure = json.loads((root / "FAILURE.json").read_text(encoding="utf-8"))
    checkpoint = json.loads((root / "checkpoint.json").read_text(encoding="utf-8"))
    assert failure["status"] == "FAILED_NON_REUSABLE"
    assert checkpoint["terminal_status"] == "FAILED_NON_REUSABLE"
    assert checkpoint["resume_status"] == "NON_REUSABLE"
    assert state["closed"] is True
    with pytest.raises(OptimizationPilotExecutionError, match="pilot_output_root_not_fresh"):
        asyncio.run(
            execute_w4_pilot(
                contract=contract,
                verified_formal_plan=formal,
                episodes=episodes,
                env={},
                output_root=root,
                state_cut_certification=certification,
                implementation_sha256="e" * 64,
                hooks=hooks,
            )
        )
