"""Provider-free production-composition gates for MEG OBSERVE_ONLY."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.membind_v31.live_block import V31LiveHooks
from paper_eval.membind_v4.mseg.graphiti_0293_audit import audit_graphiti_0293
from paper_eval.membind_v4.mseg.mutation_epoch import StateMutationEpoch
from paper_eval.membind_v4.mseg.runtime_instrumentation import (
    InstrumentationMode,
    MEGRuntimeRecorder,
    SemanticOperatorClass,
    SemanticOperatorInstance,
    WriterDomainCertificate,
)
from paper_eval.membind_v4.mseg.runtime_live import (
    MEGRuntimeLiveError,
    build_meg_observe_only_live_composition,
    build_observe_capture_contract,
    build_v31_observe_composition_proof,
    derive_observe_namespace,
)
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan
from paper_eval.s5_graphiti_controlled_fixture import build_controlled_graphiti_fixture


PROJECT = Path(__file__).resolve().parents[1]
GRAPHITI = (
    PROJECT.parent
    / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core"
)
RUN_ID = "membind-v31-opt-w4-meg-runtime-observe-20260821-test"


def _hooks(adapter_factory) -> V31LiveHooks:
    async def async_value(*_args, **_kwargs):
        return None

    return V31LiveHooks(
        runtime_builder=lambda **kwargs: kwargs,
        runtime_ready=async_value,
        namespace_probe=async_value,
        namespace_episode=lambda episode, _namespace: episode,
        source_visibility_probe=async_value,
        reference_time_to_ns=lambda _value: 0,
        adapter_factory=adapter_factory,
        close_runtime=async_value,
    )


def _writer(namespace: str) -> WriterDomainCertificate:
    return WriterDomainCertificate.create(
        namespace=namespace,
        graph_backend="neo4j",
        authorized_writer_identity="test",
        write_path_coverage=("managed-bulk",),
        expected_write_paths=("managed-bulk",),
        external_writer_policy="DENY",
        commit_observer_coverage="ALL_MANAGED_COMMITS",
        fresh_namespace=True,
        no_background_mutation=True,
    )


def test_live_composition_replaces_only_adapter_factory() -> None:
    fixture = build_controlled_graphiti_fixture()
    namespace = derive_observe_namespace(RUN_ID)
    writer = _writer(namespace)
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY, writer_domain=writer
    )
    epoch = StateMutationEpoch(
        namespace=namespace, backend_id="neo4j", epoch="test-epoch"
    )

    class Inner:
        async def prepare(self, value):
            return value

        async def bind(self, value, artifact, *, logical_time_ns):
            return value, artifact, logical_time_ns

    base_factory = lambda _runtime, _certification: object()
    base = _hooks(base_factory)
    captured = {}

    def inner_factory(_runtime, _certification, binding):
        captured["binding"] = binding
        return Inner()

    composition = build_meg_observe_only_live_composition(
        recorder=recorder,
        mutation_epoch=epoch,
        writer_domain=writer,
        stream_id="07741c45",
        base_hooks=base,
        semantic_binding_loader=lambda: fixture.binding,
        inner_adapter_factory=inner_factory,
    )
    for name in (
        "runtime_ready",
        "namespace_probe",
        "namespace_episode",
        "source_visibility_probe",
        "reference_time_to_ns",
        "close_runtime",
    ):
        assert getattr(composition.hooks, name) is getattr(base, name)
    assert composition.hooks.runtime_builder is not base.runtime_builder
    adapter = composition.hooks.adapter_factory(object(), object())
    assert callable(adapter.prepare) and callable(adapter.bind)
    assert captured["binding"] is not fixture.binding
    assert composition.execution_policy_changed is False


def test_live_runtime_builder_adds_only_passive_semantic_causal_metadata() -> None:
    namespace = derive_observe_namespace(RUN_ID)
    writer = _writer(namespace)
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY, writer_domain=writer
    )
    epoch = StateMutationEpoch(
        namespace=namespace, backend_id="neo4j", epoch="test-epoch"
    )
    base = _hooks(lambda **kwargs: kwargs)
    composition = build_meg_observe_only_live_composition(
        recorder=recorder,
        mutation_epoch=epoch,
        writer_domain=writer,
        stream_id="07741c45",
        base_hooks=base,
        semantic_binding_loader=lambda: build_controlled_graphiti_fixture().binding,
        inner_adapter_factory=lambda _runtime, _certification, _binding: object(),
    )
    operator = SemanticOperatorInstance.create(
        graph_id=namespace,
        stream_id="07741c45",
        source_sequence=0,
        semantic_operator_type="NODE_EXTRACTION",
        classification=SemanticOperatorClass.EVIDENCE_DERIVED,
        parent_semantic_operator_ids=(),
        child_ordinal=0,
        semantic_input_identity={"fixture": "causal"},
    )
    recorder.materialize(operator, immutable_inputs_exist=True, state_satisfiable=True)
    recorder.start(operator.semantic_operator_id)
    recorder.end(operator.semantic_operator_id)
    # Start/end above completes the operator; use a direct scope on a fresh
    # instance to exercise the provider while preserving readiness semantics.
    operator2 = SemanticOperatorInstance.create(
        graph_id=namespace,
        stream_id="07741c45",
        source_sequence=0,
        semantic_operator_type="NODE_EXTRACTION",
        classification=SemanticOperatorClass.EVIDENCE_DERIVED,
        parent_semantic_operator_ids=(),
        child_ordinal=1,
        semantic_input_identity={"fixture": "causal-2"},
    )
    recorder.materialize(operator2, immutable_inputs_exist=True, state_satisfiable=True)
    with recorder.operator_scope(operator2.semantic_operator_id):
        runtime_value = composition.hooks.runtime_builder(policy="CACHE_AFFINE")
        provider = runtime_value["causal_metadata_provider"]
        metadata = provider()
    assert callable(provider)
    assert metadata["operator_id"] == operator2.semantic_operator_id
    assert metadata["operator_role"] == "meg.NODE_EXTRACTION"
    assert metadata["operator_phase"] == "MEG_RUNTIME"
    assert runtime_value["policy"] == "CACHE_AFFINE"


def test_capture_contract_pins_normal_v31_configuration_and_prefix() -> None:
    plan = verify_membind_v31_method_plan(
        json.loads(
            (PROJECT / "artifacts/paper_eval/membind_v31/V31_METHOD_PLAN.json").read_text()
        )
    )
    audit = audit_graphiti_0293(GRAPHITI)
    proof = build_v31_observe_composition_proof(
        project_root=PROJECT, graphiti_source_hashes=audit["source_hashes"]
    )
    contract = build_observe_capture_contract(
        verified_plan=plan,
        run_id=RUN_ID,
        output_root=PROJECT / "unused-test-output",
        source_count=3,
        composition_proof=proof,
    )
    assert proof["saga_none"] is True
    assert proof["community_update_invoked"] is False
    assert contract["source_sequences"] == [0, 1, 2]
    assert contract["arrival_offsets_ns"] == [0, 41_811_191_012, 83_622_382_024]
    assert (
        contract["compile_workers"],
        contract["lookahead"],
        contract["bind_workers"],
        contract["global_llm_admission_k"],
    ) == (2, 4, 1, 2)
    assert contract["mode"] == "OBSERVE_ONLY"
    assert contract["shadow_reads_authorized"] is False
    assert contract["scheduler_change_authorized"] is False


def test_capture_contract_rejects_unapproved_source_count() -> None:
    plan = json.loads(
        (PROJECT / "artifacts/paper_eval/membind_v31/V31_METHOD_PLAN.json").read_text()
    )
    with pytest.raises(MEGRuntimeLiveError, match="meg_runtime_source_count_invalid"):
        build_observe_capture_contract(
            verified_plan=plan,
            run_id=RUN_ID,
            output_root=PROJECT / "unused-test-output",
            source_count=4,
            composition_proof={},
        )
