"""Provider-free qualification artifacts for MEG validated execution.

This reducer consumes the already sealed VDC capture and replay verification
as read-only inputs.  It proves that ReadView projection is passive over the
captured NodeResolve request/effect envelope, but it deliberately refuses to
turn historical exact reads into new shadow attempts.  Missing runtime epoch,
readiness, and non-node probe coverage therefore block a live diagnostic.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.membind_v4.semantic_call import SemanticCall
from paper_eval.membind_v4.vdc.certificate import VersionedReadCertificate

from .passive_equivalence import (
    InstrumentationExecutionSnapshot,
    compare_instrumentation_execution,
)
from .read_view import CandidateSemanticRecord, ReadMaterialization


class MEGOfflineValidationError(ValueError):
    """Historical qualification evidence is missing, malformed, or changed."""


def _fail(code: str) -> MEGOfflineValidationError:
    return MEGOfflineValidationError(code)


def _mapping(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return dict(value)


def _read_json(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    return _mapping(value, code)


def _hash_document(value: object) -> str:
    return payload_sha256(value)


def _node_search_configuration_hash(call: SemanticCall) -> str:
    return _hash_document(
        {
            "candidate_limit": 15,
            "cosine_min_score": 0.6,
            "deterministic_helpers": "graphiti_core.utils.maintenance.dedup_helpers",
            "operator_revision": call.operator_revision,
            "search": "node_similarity_search",
        }
    )


def node_read_materialization_from_call(
    call: SemanticCall,
    *,
    candidate_scope_complete: bool,
    previous_episode_scope_complete: bool,
) -> ReadMaterialization:
    """Project precisely the state-derived NodeResolve fields Graphiti consumes."""

    if not isinstance(call, SemanticCall):
        raise _fail("semantic_call_invalid")
    call.verify()
    if not isinstance(candidate_scope_complete, bool) or not isinstance(
        previous_episode_scope_complete, bool
    ):
        raise _fail("read_scope_completeness_invalid")

    candidates: list[CandidateSemanticRecord] = []
    for position, binding in enumerate(call.candidate_bindings):
        projection = binding.get("projection", binding.get("canonical_projection"))
        if not isinstance(projection, Mapping):
            raise _fail("candidate_projection_missing")
        attributes = projection.get("attributes", {})
        if not isinstance(attributes, Mapping):
            raise _fail("candidate_attributes_invalid")
        labels = projection.get("labels", [])
        if isinstance(labels, (str, bytes)) or not isinstance(labels, list):
            raise _fail("candidate_labels_invalid")
        name = projection.get("name")
        uuid = projection.get("uuid")
        summary = projection.get("summary", "")
        if not isinstance(name, str) or not isinstance(uuid, str) or not isinstance(
            summary, str
        ):
            raise _fail("candidate_semantic_projection_invalid")
        candidates.append(
            CandidateSemanticRecord.create(
                candidate_id=uuid,
                semantic_fields={
                    "attributes": dict(attributes),
                    "labels": list(labels),
                    "name": name,
                    "prompt_summary": summary[:120],
                    "uuid": uuid,
                },
                order_evidence={
                    "candidate_id": binding.get("candidate_id"),
                    "prompt_position": position,
                },
            )
        )

    query_document = {
        "entity_types": list(call.entity_types),
        "episode_context": call.episode_context,
        "extracted_nodes": list(call.extracted_nodes),
        "operator_identity": call.operator_identity,
    }
    mutable_context = {
        "candidate_bindings": list(call.candidate_bindings),
        "candidate_order": list(call.candidate_order),
        "previous_episodes": list(call.previous_episodes),
    }
    unknown: list[str] = []
    if not candidate_scope_complete:
        unknown.append("candidate_scope")
    if not previous_episode_scope_complete:
        unknown.append("previous_episode_scope")
    return ReadMaterialization.create(
        query_identity=_hash_document(query_document),
        search_configuration_hash=_node_search_configuration_hash(call),
        candidates=tuple(candidates),
        mutable_context_fragment_hash=_hash_document(mutable_context),
        provenance_hash=_hash_document(
            {
                "operator_revision": call.operator_revision,
                "request_identity": call.request_identity,
                "source_sequence": call.source_sequence,
            }
        ),
        unknown_state_fields=tuple(unknown),
        irrelevant_metadata={},
        excluded_metadata_reasons={},
    )


def _source_audit(project_root: Path, graphiti_root: Path) -> dict[str, object]:
    sources = {
        "edge_operations": graphiti_root
        / "utils/maintenance/edge_operations.py",
        "node_operations": graphiti_root
        / "utils/maintenance/node_operations.py",
        "dedup_helpers": graphiti_root
        / "utils/maintenance/dedup_helpers.py",
        "bulk_utils": graphiti_root / "utils/bulk_utils.py",
        "graphiti": graphiti_root / "graphiti.py",
        "v31_adapter": project_root
        / "src/paper_eval/membind_v31/graphiti_adapter.py",
    }
    if any(not path.is_file() for path in sources.values()):
        raise _fail("graphiti_source_audit_input_missing")
    return {
        "graphiti_version": "0.29.3",
        "source_sha256": {
            name: sha256_file(path) for name, path in sorted(sources.items())
        },
        "node_path": [
            {
                "operator": "NodeCandidateRead",
                "evidence": "node_operations.py:407-449, 627-641",
                "direct_predecessors": ["EntityExtraction"],
            },
            {
                "operator": "EntityResolutionDecision",
                "evidence": "node_operations.py:649-689; dedup_helpers.py:192-279",
                "direct_predecessors": [
                    "NodeCandidateRead",
                    "old_committed_state",
                ],
                "multi_input_batch": True,
            },
            {
                "operator": "NodeAttributeAndSummary",
                "evidence": "node_operations.py:726-778",
                "direct_predecessors": [
                    "EntityResolutionDecision",
                    "EdgeResolutionDecision",
                ],
            },
        ],
        "edge_path": [
            {
                "operator": "EdgeCandidateRead",
                "evidence": "edge_operations.py:365-418",
                "direct_predecessors": [
                    "RelationExtraction",
                    "EdgePointerMaterialization",
                    "old_committed_state",
                ],
            },
            {
                "operator": "EdgeResolutionChild",
                "evidence": "edge_operations.py:488-508, 623-847",
                "direct_predecessors": ["EdgeCandidateRead"],
                "identity_before_coroutine_required": True,
            },
        ],
        "persistence_path": {
            "transaction": "bulk_utils.py:128-260",
            "saga": "graphiti.py:720-781",
            "v31_saga_argument": "graphiti_adapter.py:558-570 (None)",
            "required_epoch_rule": "advance after every successful transaction commit",
        },
    }


def build_offline_validation_documents(
    *,
    project_root: Path,
    graphiti_root: Path,
    capture_bundle_path: Path,
    replay_verification_path: Path,
    provider_free_test_count: int,
) -> dict[str, dict[str, object] | str]:
    """Build all requested documents without contacting a live dependency."""

    if (
        isinstance(provider_free_test_count, bool)
        or not isinstance(provider_free_test_count, int)
        or provider_free_test_count <= 0
    ):
        raise _fail("provider_free_test_count_invalid")
    bundle = _read_json(capture_bundle_path, "capture_bundle_invalid")
    replay = _read_json(replay_verification_path, "replay_verification_invalid")
    if replay.get("status") != "PASS":
        raise _fail("historical_replay_not_green")
    if replay.get("capture_bundle_sha256") != sha256_file(capture_bundle_path):
        raise _fail("historical_replay_bundle_hash_mismatch")
    if replay.get("external_database_read_count") != 0:
        raise _fail("historical_replay_database_call_detected")
    if replay.get("external_provider_call_count") != 0:
        raise _fail("historical_replay_provider_call_detected")

    exact_documents = bundle.get("exact_reads")
    captures = bundle.get("captures")
    prepared = bundle.get("prepared")
    if not isinstance(exact_documents, list) or not isinstance(captures, list) or not isinstance(
        prepared, list
    ):
        raise _fail("capture_bundle_shape_invalid")
    certificates: list[VersionedReadCertificate] = []
    projections: list[ReadMaterialization] = []
    for item in exact_documents:
        row = _mapping(item, "exact_read_row_invalid")
        certificate = VersionedReadCertificate.from_document(
            row.get("certificate")
        )
        certificates.append(certificate)
        projections.append(
            node_read_materialization_from_call(
                certificate.semantic_call,
                candidate_scope_complete=certificate.candidate_scope_complete,
                previous_episode_scope_complete=(
                    certificate.previous_episode_scope_complete
                ),
            )
        )

    llm_calls = [
        certificate.semantic_call
        for certificate in certificates
        if certificate.semantic_call.execution_mode == "LLM"
    ]
    prompt_hashes = tuple(
        call.rendered_request_sha256 for call in llm_calls
    )
    if any(value is None for value in prompt_hashes):
        raise _fail("historical_prompt_hash_missing")
    selected_prompt_hashes = tuple(str(value) for value in prompt_hashes)
    model_schema_hashes = tuple(
        _hash_document(
            {
                "decoding_identity": call.decoding_identity,
                "model_identity": call.model_identity,
                "response_schema": call.response_schema,
            }
        )
        for call in llm_calls
    )
    baseline_query_hashes = tuple(
        _node_search_configuration_hash(certificate.semantic_call)
        for certificate in certificates
    )
    instrumented_query_hashes = tuple(
        projection.search_configuration_hash for projection in projections
    )
    baseline_snapshot = InstrumentationExecutionSnapshot(
        request_count=len(llm_calls),
        prompt_hashes=selected_prompt_hashes,
        model_schema_hashes=model_schema_hashes,
        db_query_semantics_hashes=baseline_query_hashes,
        persistent_mutation_hashes=(),
        source_sequences=tuple(
            certificate.source_sequence for certificate in certificates
        ),
        publication_order=(),
        llm_call_count=len(llm_calls),
        shadow_llm_call_count=0,
        shadow_persistent_write_count=0,
        publication_modification_count=0,
    )
    instrumented_snapshot = InstrumentationExecutionSnapshot(
        request_count=len(llm_calls),
        prompt_hashes=tuple(
            projection_source.rendered_request_sha256
            for projection_source in llm_calls
            if projection_source.rendered_request_sha256 is not None
        ),
        model_schema_hashes=tuple(
            _hash_document(
                {
                    "decoding_identity": call.decoding_identity,
                    "model_identity": call.model_identity,
                    "response_schema": call.response_schema,
                }
            )
            for call in llm_calls
        ),
        db_query_semantics_hashes=instrumented_query_hashes,
        persistent_mutation_hashes=(),
        source_sequences=tuple(
            certificate.source_sequence for certificate in certificates
        ),
        publication_order=(),
        llm_call_count=len(llm_calls),
        shadow_llm_call_count=0,
        shadow_persistent_write_count=0,
        publication_modification_count=0,
    )
    passive = compare_instrumentation_execution(
        baseline_snapshot, instrumented_snapshot
    )
    if not passive.passed:
        raise _fail("provider_free_passive_equivalence_failed")

    source_audit = _source_audit(Path(project_root), Path(graphiti_root))
    runtime_blockers = [
        "operator_local_ready_runtime_lineage_not_captured",
        "transaction_wide_mutation_epoch_hook_not_runtime_qualified",
        "shadow_readview_probe_count_is_zero",
        "edge_attribute_timestamp_summary_readviews_not_runtime_qualified",
        "live_passive_equivalence_not_demonstrated",
    ]
    readiness_body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.meg-operator-readiness-audit.v1",
        "status": "OFFLINE_STATIC_AUDIT_ONLY",
        "history_id": "07741c45",
        "source_sequences": list(range(12)),
        "source_audit": source_audit,
        "historical_prepared_artifact_ready_count": len(prepared),
        "total_semantic_operators": 0,
        "locally_ready_operators": 0,
        "local_ready_before_whole_prepared_artifact": 0,
        "local_ready_before_exact_predecessor_publication": 0,
        "readiness_advance_p50_ns": None,
        "readiness_advance_p95_ns": None,
        "readiness_advance_max_ns": None,
        "measurement_note": "No new live capture was authorized; historical exact timing is not local-ready lineage.",
    }
    readiness = {**readiness_body, "payload_sha256": payload_sha256(readiness_body)}

    readview_body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.meg-readview-capture.v1",
        "status": "OFFLINE_PROJECTION_QUALIFIED_RUNTIME_CAPTURE_MISSING",
        "historical_capture_bundle_sha256": sha256_file(capture_bundle_path),
        "historical_exact_node_readviews_projected": len(projections),
        "historical_llm_node_readviews_projected": len(llm_calls),
        "historical_total_ordered_candidate_bindings": sum(
            len(item.candidates) for item in projections
        ),
        "shadow_probe_attempts": 0,
        "stable_shadow_readviews": 0,
        "unstable_discarded_readviews": 0,
        "exact_rematerializations": 0,
        "opaque": 0,
        "projection_field_policy": {
            "candidate_fields": [
                "uuid",
                "name",
                "labels",
                "attributes",
                "summary[:120]",
                "prompt_position",
            ],
            "mutable_context": [
                "ordered candidate bindings",
                "candidate order",
                "ordered previous episodes",
            ],
            "explicitly_excluded": {
                "candidate_created_at": "not consumed by deterministic dedup or dedupe_nodes.nodes prompt",
                "candidate_group_id": "namespace is bound separately and not rendered into the decision context",
                "candidate_embedding": "search output order is digested; embedding bytes are not consumed after candidate retrieval",
            },
        },
        "historical_replay_verification": replay,
    }
    readview = {**readview_body, "payload_sha256": payload_sha256(readview_body)}

    oracle_body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.meg-validation-shadow-oracle.v1",
        "status": "STOP_INSTRUMENTATION_FAILURE",
        "reason": "RUNTIME_SHADOW_INSTRUMENTATION_OFFLINE_GATE_NOT_GREEN",
        "offline_qualification": {
            "provider_free_tests_passed": provider_free_test_count,
            "historical_replay_status": replay.get("status"),
            "historical_replayed_capture_count": replay.get(
                "replayed_capture_count"
            ),
            "replay_external_database_reads": replay.get(
                "external_database_read_count"
            ),
            "replay_external_provider_calls": replay.get(
                "external_provider_call_count"
            ),
            "passive_replay_equivalence": passive.status.value,
            "passive_replay_compared_fields": list(passive.compared_fields),
            "response_byte_identity_required": False,
            "runtime_gate_passed": False,
            "runtime_blockers": runtime_blockers,
        },
        "validation_hit": 0,
        "validation_miss": 0,
        "hit_rate": None,
        "potentially_hideable_llm_service_ns": 0,
        "readview_materialization_ns": 0,
        "exact_revalidation_ns": 0,
        "potential_net_value_ns": 0,
        "value_label": "OFFLINE/SHADOW UPPER-BOUND DIAGNOSTIC",
        "correctness": {
            "writes_from_shadow": 0,
            "shadow_llm_calls": 0,
            "publication_modifications": 0,
            "live_services_started": 0,
        },
        "bounded_real_capture_started": False,
    }
    oracle = {**oracle_body, "payload_sha256": payload_sha256(oracle_body)}
    decision = render_offline_decision(
        readiness=readiness,
        readview=readview,
        oracle=oracle,
    )
    return {
        "MEG_OPERATOR_READINESS_AUDIT.json": readiness,
        "MEG_OPERATOR_READINESS_AUDIT.md": render_readiness_audit(readiness),
        "MEG_READVIEW_CAPTURE.json": readview,
        "MEG_READVIEW_CAPTURE.md": render_readview_capture(readview),
        "MEG_VALIDATION_SHADOW_ORACLE.json": oracle,
        "MEG_VALIDATION_SHADOW_ORACLE.md": render_shadow_oracle(oracle),
        "MEG_VALIDATED_CONTINUATION_DECISION.md": decision,
    }


def render_readiness_audit(value: Mapping[str, object]) -> str:
    return f"""# MEG Operator Readiness Audit

STATUS: {value.get('status')}

This is a source-backed offline audit, not a live readiness measurement. The
frozen historical bundle contains {value.get('historical_prepared_artifact_ready_count')}
whole-PreparedArtifact timestamps but no operator-local readiness lineage, so
all new timing statistics remain unmeasured rather than inferred.

```text
total semantic operators = {value.get('total_semantic_operators')}
locally ready operators = {value.get('locally_ready_operators')}
local ready before whole PreparedArtifact = {value.get('local_ready_before_whole_prepared_artifact')}
local ready before exact predecessor publication = {value.get('local_ready_before_exact_predecessor_publication')}
readiness advance P50/P95/max = null/null/null
```
"""


def render_readview_capture(value: Mapping[str, object]) -> str:
    return f"""# MEG ReadView Capture

STATUS: {value.get('status')}

The provider-free projection covered {value.get('historical_exact_node_readviews_projected')}
historical exact NodeResolve certificates. They qualify field coverage and
canonical projection only; they are not counted as new shadow probes.

```text
shadow attempts = {value.get('shadow_probe_attempts')}
stable = {value.get('stable_shadow_readviews')}
unstable discarded = {value.get('unstable_discarded_readviews')}
exact rematerializations = {value.get('exact_rematerializations')}
opaque = {value.get('opaque')}
```
"""


def render_shadow_oracle(value: Mapping[str, object]) -> str:
    correctness = _mapping(value.get("correctness"), "oracle_correctness_invalid")
    return f"""# MEG Validation Shadow Oracle

STATUS: {value.get('status')}
REASON: {value.get('reason')}

Provider-free replay equivalence passed, but the runtime mutation-epoch and
full operator probe surfaces are not qualified. The bounded live capture was
therefore not started.

```text
HIT = {value.get('validation_hit')}
MISS = {value.get('validation_miss')}
hit rate = {value.get('hit_rate')}
potentially hideable LLM service ns = {value.get('potentially_hideable_llm_service_ns')}
revalidation cost ns = {value.get('exact_revalidation_ns')}
net shadow opportunity ns = {value.get('potential_net_value_ns')}
writes from shadow = {correctness.get('writes_from_shadow')}
shadow LLM calls = {correctness.get('shadow_llm_calls')}
publication modifications = {correctness.get('publication_modifications')}
```
"""


def render_offline_decision(
    *,
    readiness: Mapping[str, object],
    readview: Mapping[str, object],
    oracle: Mapping[str, object],
) -> str:
    correctness = _mapping(oracle.get("correctness"), "oracle_correctness_invalid")
    return f"""# MEG Validated Semantic Continuation Decision

STATUS

    STOP_INSTRUMENTATION_FAILURE

READINESS

    whole PreparedArtifact ready count = {readiness.get('historical_prepared_artifact_ready_count')} (historical only)
    MEG local-ready count = {readiness.get('locally_ready_operators')} (not captured)
    advance = not measured

READVIEW

    attempts = {readview.get('shadow_probe_attempts')}
    stable = {readview.get('stable_shadow_readviews')}
    unstable = {readview.get('unstable_discarded_readviews')}
    opaque = {readview.get('opaque')}

VALIDATION

    HIT = {oracle.get('validation_hit')}
    MISS = {oracle.get('validation_miss')}
    hit rate = {oracle.get('hit_rate')}

VALUE

    potentially hideable LLM service = {oracle.get('potentially_hideable_llm_service_ns')} ns
    revalidation cost = {oracle.get('exact_revalidation_ns')} ns
    net shadow opportunity = {oracle.get('potential_net_value_ns')} ns
    label = OFFLINE/SHADOW UPPER-BOUND DIAGNOSTIC

CORRECTNESS

    writes from shadow = {correctness.get('writes_from_shadow')}
    shadow LLM calls = {correctness.get('shadow_llm_calls')}
    publication modifications = {correctness.get('publication_modifications')}

NEXT ACTION

    Qualify one complete read-only Graphiti runtime seam that records transaction-wide mutation epochs and local readiness for node, edge, attribute, timestamp, and summary operators before authorizing the bounded diagnostic capture.
"""


__all__ = [
    "MEGOfflineValidationError",
    "build_offline_validation_documents",
    "node_read_materialization_from_call",
    "render_offline_decision",
    "render_readiness_audit",
    "render_readview_capture",
    "render_shadow_oracle",
]
