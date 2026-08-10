"""State-gated live orchestration for Protocol v1.3 H0-B and H0-C.

This path is intentionally separate from the legacy experiment runner and the
direct-call H0-A canary.  It owns one stage ledger, full-stack readiness, fresh
Graphiti resources per history, and durable per-source checkpoints.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import inspect
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping

from h0_completion import validate_h0_prior_phase_terminal_completion
from h0_credentials import H0ProjectCredentialLoader
from h0_graphiti_adapter import (
    H0_EMBEDDING_BASE_URL,
    H0_EMBEDDING_VLLM_VERSION,
    H0_NEO4J_DATABASE,
    H0_NEO4J_URI,
    H0GraphitiHistoryFactory,
    close_h0_graphiti_history,
    evaluate_h0_retrieval,
)
from h0_full_history_completion import validate_h0_b_terminal_completion
from h0_phase_runner import (
    H0SemanticEvidenceCollector,
    run_full_history,
    run_h0_full_history_phase,
)
from h0_runtime import (
    H0AttemptLedger,
    H0CheckpointStore,
    H0InfrastructureError,
    H0ManifestError,
    H0QualificationError,
    H0StateGateError,
    authorize_h0_live_entry,
    canonical_json_sha256,
    load_h0_calibration_corpus,
)
from h0_stage_readiness import run_h0_stage_readiness


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = ROOT / "CURRENT_STATE.json"
DEFAULT_ARTIFACTS_ROOT = ROOT / "artifacts/h0_runs"
H0_B_REPLACEMENT_ATTEMPT_ID = "h0-q1-b-20260809-replacement-001"
H0_B_INFRASTRUCTURE_RERUN_ATTEMPT_ID = (
    "h0-q1-b-20260810-replacement-002"
)
H0_B_POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID = (
    "h0-q1-b-20260810-replacement-003"
)
H0_B_INVALIDATED_ATTEMPT_ID = "h0-q1-b-20260809-attempt-001"
H0_B_REPAIR_DECISION_PATH = (
    "artifacts/h0_protocol_repair/decisions/"
    "q1_h0_b_harness_compatibility_repair.json"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_H0_B_HARNESS_REPAIR_FIELDS = {
    "schema_version",
    "protocol_version",
    "candidate_id",
    "phase",
    "decision_path",
    "decision_sha256",
    "decision_result_blind",
    "prior_model_workload_output_observed",
    "repair_required_independent_of_model_output",
    "scientific_configuration_unchanged",
    "one_shot_whole_stage_replacement",
    "replacement_attempt_id",
    "invalidated_stage_attempt_id",
    "invalidated_checkpoint_index_sha256",
    "failure_report_sha256",
    "old_attempt_qualification_reusable",
    "old_and_new_trial_counts_mergeable",
    "prior_manifest_index_sha256",
    "repaired_manifest_index_sha256",
    "secrets_persisted",
}
_H0_B_HARNESS_REPAIR_SHA_FIELDS = (
    "decision_sha256",
    "invalidated_checkpoint_index_sha256",
    "failure_report_sha256",
    "prior_manifest_index_sha256",
    "repaired_manifest_index_sha256",
)
_H0_B_INFRASTRUCTURE_RERUN_FIELDS = {
    "schema_version",
    "protocol_version",
    "candidate_id",
    "phase",
    "decision_path",
    "decision_sha256",
    "interrupted_stage_attempt_id",
    "interrupted_checkpoint_index_sha256",
    "interrupted_stop_reason",
    "prior_harness_repair_admission_sha256",
    "replacement_attempt_id",
    "one_shot_whole_stage_replacement",
    "resume_interrupted_attempt_allowed",
    "prior_attempt_qualification_reusable",
    "old_and_new_trial_counts_mergeable",
    "scientific_configuration_unchanged",
    "prior_manifest_index_sha256",
    "recovered_manifest_index_sha256",
    "secrets_persisted",
}
_H0_B_POST_WORKLOAD_REPAIR_FIELDS = {
    "schema_version",
    "protocol_version",
    "candidate_id",
    "phase",
    "decision_path",
    "decision_sha256",
    "decision_result_blind",
    "prior_model_workload_output_observed",
    "repair_required_independent_of_model_response_content",
    "scientific_configuration_unchanged",
    "one_shot_whole_stage_replacement",
    "replacement_attempt_id",
    "invalidated_stage_attempt_id",
    "invalidated_checkpoint_index_sha256",
    "failure_segment_sha256",
    "source_checkpoint_sha256",
    "live_log_sha256",
    "offline_probe_sha256",
    "prior_harness_repair_admission_sha256",
    "prior_infrastructure_rerun_admission_sha256",
    "old_attempt_qualification_reusable",
    "old_and_new_trial_counts_mergeable",
    "resume_failed_attempt_allowed",
    "prior_manifest_index_sha256",
    "repaired_manifest_index_sha256",
    "secrets_persisted",
}


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class _CachedAllCredentials:
    """Read the explicit project file at most once after authorization."""

    def __init__(self, source: Callable[[], Mapping[str, Any]]) -> None:
        self.source = source
        self.value: dict[str, dict[str, Any]] | None = None

    def __call__(self) -> dict[str, dict[str, Any]]:
        if self.value is None:
            raw = self.source()
            if not isinstance(raw, Mapping):
                raise H0ManifestError("H0 full-history credential loader is invalid")
            value: dict[str, dict[str, Any]] = {}
            for name in ("construction", "embedding", "neo4j"):
                section = raw.get(name)
                if not isinstance(section, Mapping):
                    raise H0ManifestError(
                        f"H0 full-history {name} credentials are invalid"
                    )
                value[name] = dict(section)
            self.value = value
        return deepcopy(self.value)


class H0StageReadinessCheckpointSink:
    """Synchronously make every full-stack readiness event durable."""

    def __init__(self, store: H0CheckpointStore) -> None:
        self.store = store
        self.persisted_count = 0

    def __call__(self, event: Mapping[str, Any]) -> None:
        if not isinstance(event, Mapping):
            raise H0ManifestError("H0 stage readiness event is invalid")
        check = event.get("check")
        if not isinstance(check, str) or not check:
            raise H0ManifestError("H0 stage readiness check name is invalid")
        self.store.record_segment(
            "stage_readiness_check",
            f"{self.persisted_count:03d}-{check}",
            dict(event),
        )
        self.persisted_count += 1


def _load_h0_corpus(root: Path) -> Any:
    split_path = root / "artifacts/dataset/frozen_split_v1_3.json"
    try:
        split = json.loads(split_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise H0ManifestError("H0 calibration split is missing or invalid") from None
    source = split.get("source_path") if isinstance(split, Mapping) else None
    if not isinstance(source, str) or not source:
        raise H0ManifestError("H0 calibration split has no source path")
    return load_h0_calibration_corpus(split_path, source)


def _load_runtime_definition(authorization: Mapping[str, Any], *, root: Path) -> Any:
    from h0_live_preflight import load_authorized_h0_runtime_definition

    return load_authorized_h0_runtime_definition(authorization, root=root)


def _extract_h0_b_harness_repair_admission(
    authorization: Mapping[str, Any],
    *,
    stage_attempt_id: str,
    candidate_id: str,
    phase: str,
) -> Mapping[str, Any] | None:
    """Validate the immutable first repair layer for either exact rerun."""

    if phase != "H0-B":
        return None
    raw = authorization.get("repair_admission")
    admission = dict(raw) if isinstance(raw, Mapping) else {}
    exact = (
        candidate_id == "Q1"
        and authorization.get("candidate_id") == "Q1"
        and authorization.get("phase") == "H0-B"
        and authorization.get("authorized_stage_attempt_id")
        == stage_attempt_id
        and set(admission) == _H0_B_HARNESS_REPAIR_FIELDS
        and admission.get("schema_version")
        == "membind.h0.harness-repair-admission.v1"
        and admission.get("protocol_version") == "current-validation-v1.3"
        and admission.get("candidate_id") == "Q1"
        and admission.get("phase") == "H0-B"
        and isinstance(admission.get("decision_path"), str)
        and ".env" not in Path(str(admission.get("decision_path"))).parts
        and "gpt55_temporary"
        not in Path(str(admission.get("decision_path"))).parts
        and admission.get("decision_result_blind") is False
        and admission.get("prior_model_workload_output_observed") is False
        and admission.get("repair_required_independent_of_model_output") is True
        and admission.get("scientific_configuration_unchanged") is True
        and admission.get("one_shot_whole_stage_replacement") is True
        and admission.get("replacement_attempt_id")
        == H0_B_REPLACEMENT_ATTEMPT_ID
        and admission.get("invalidated_stage_attempt_id")
        == H0_B_INVALIDATED_ATTEMPT_ID
        and admission.get("old_attempt_qualification_reusable") is False
        and admission.get("old_and_new_trial_counts_mergeable") is False
        and (
            (
                stage_attempt_id == H0_B_REPLACEMENT_ATTEMPT_ID
                and admission.get("repaired_manifest_index_sha256")
                == authorization.get("resolved_manifest_index_sha256")
            )
            or (
                stage_attempt_id
                in {
                    H0_B_INFRASTRUCTURE_RERUN_ATTEMPT_ID,
                    H0_B_POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID,
                }
                and isinstance(
                    authorization.get("infrastructure_rerun_admission"), Mapping
                )
                and admission.get("repaired_manifest_index_sha256")
                == authorization["infrastructure_rerun_admission"].get(
                    "prior_manifest_index_sha256"
                )
            )
        )
        and admission.get("secrets_persisted") is False
        and all(
            _SHA256_RE.fullmatch(str(admission.get(field) or "")) is not None
            for field in _H0_B_HARNESS_REPAIR_SHA_FIELDS
        )
    )
    if not exact:
        raise H0StateGateError("H0-B harness replacement admission denied")
    return raw


def _extract_h0_b_infrastructure_rerun_admission(
    authorization: Mapping[str, Any],
    *,
    stage_attempt_id: str,
    repair_admission: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Validate the second, independent admission for replacement-002."""

    raw = authorization.get("infrastructure_rerun_admission")
    if stage_attempt_id not in {
        H0_B_INFRASTRUCTURE_RERUN_ATTEMPT_ID,
        H0_B_POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID,
    }:
        if raw is not None:
            raise H0StateGateError("unexpected H0-B infrastructure rerun admission")
        return None
    admission = dict(raw) if isinstance(raw, Mapping) else {}
    exact = (
        set(admission) == _H0_B_INFRASTRUCTURE_RERUN_FIELDS
        and admission.get("schema_version")
        == "membind.h0.infrastructure-rerun-admission.v1"
        and admission.get("protocol_version") == "current-validation-v1.3"
        and admission.get("candidate_id") == "Q1"
        and admission.get("phase") == "H0-B"
        and admission.get("replacement_attempt_id")
        == H0_B_INFRASTRUCTURE_RERUN_ATTEMPT_ID
        and admission.get("interrupted_stage_attempt_id")
        == H0_B_REPLACEMENT_ATTEMPT_ID
        and admission.get("interrupted_stop_reason") == "vllm_unreachable"
        and admission.get("prior_harness_repair_admission_sha256")
        == canonical_json_sha256(repair_admission)
        and admission.get("prior_manifest_index_sha256")
        == repair_admission.get("repaired_manifest_index_sha256")
        and admission.get("recovered_manifest_index_sha256")
        == (
            authorization.get("resolved_manifest_index_sha256")
            if stage_attempt_id == H0_B_INFRASTRUCTURE_RERUN_ATTEMPT_ID
            else (
                authorization.get("post_workload_repair_admission", {}).get(
                    "prior_manifest_index_sha256"
                )
                if isinstance(
                    authorization.get("post_workload_repair_admission"), Mapping
                )
                else None
            )
        )
        and admission.get("prior_manifest_index_sha256")
        != admission.get("recovered_manifest_index_sha256")
        and admission.get("one_shot_whole_stage_replacement") is True
        and admission.get("resume_interrupted_attempt_allowed") is False
        and admission.get("prior_attempt_qualification_reusable") is False
        and admission.get("old_and_new_trial_counts_mergeable") is False
        and admission.get("scientific_configuration_unchanged") is True
        and admission.get("secrets_persisted") is False
        and all(
            _SHA256_RE.fullmatch(str(admission.get(field) or "")) is not None
            for field in (
                "decision_sha256",
                "interrupted_checkpoint_index_sha256",
                "prior_harness_repair_admission_sha256",
                "prior_manifest_index_sha256",
                "recovered_manifest_index_sha256",
            )
        )
    )
    if not exact:
        raise H0StateGateError("H0-B infrastructure rerun admission denied")
    return raw


def _extract_h0_b_post_workload_repair_admission(
    authorization: Mapping[str, Any],
    *,
    stage_attempt_id: str,
    repair_admission: Mapping[str, Any],
    infrastructure_rerun_admission: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Validate the third, non-blind admission for replacement-003."""

    raw = authorization.get("post_workload_repair_admission")
    if stage_attempt_id != H0_B_POST_WORKLOAD_REPLACEMENT_ATTEMPT_ID:
        if raw is not None:
            raise H0StateGateError("unexpected H0-B post-workload admission")
        return None
    admission = dict(raw) if isinstance(raw, Mapping) else {}
    infrastructure = (
        dict(infrastructure_rerun_admission)
        if isinstance(infrastructure_rerun_admission, Mapping)
        else {}
    )
    decision_path = admission.get("decision_path")
    exact = (
        set(admission) == _H0_B_POST_WORKLOAD_REPAIR_FIELDS
        and admission.get("schema_version")
        == "membind.h0.post-workload-harness-repair-admission.v1"
        and admission.get("protocol_version") == "current-validation-v1.3"
        and admission.get("candidate_id") == "Q1"
        and admission.get("phase") == "H0-B"
        and admission.get("decision_result_blind") is False
        and admission.get("prior_model_workload_output_observed") is True
        and admission.get(
            "repair_required_independent_of_model_response_content"
        )
        is True
        and admission.get("scientific_configuration_unchanged") is True
        and admission.get("one_shot_whole_stage_replacement") is True
        and admission.get("replacement_attempt_id") == stage_attempt_id
        and admission.get("invalidated_stage_attempt_id")
        == H0_B_INFRASTRUCTURE_RERUN_ATTEMPT_ID
        and admission.get("prior_harness_repair_admission_sha256")
        == canonical_json_sha256(repair_admission)
        and admission.get("prior_infrastructure_rerun_admission_sha256")
        == canonical_json_sha256(infrastructure)
        and admission.get("old_attempt_qualification_reusable") is False
        and admission.get("old_and_new_trial_counts_mergeable") is False
        and admission.get("resume_failed_attempt_allowed") is False
        and admission.get("prior_manifest_index_sha256")
        == infrastructure.get("recovered_manifest_index_sha256")
        and admission.get("repaired_manifest_index_sha256")
        == authorization.get("resolved_manifest_index_sha256")
        and admission.get("prior_manifest_index_sha256")
        != admission.get("repaired_manifest_index_sha256")
        and admission.get("secrets_persisted") is False
        and isinstance(decision_path, str)
        and decision_path
        == "artifacts/h0_protocol_repair/decisions/"
        "q1_h0_b_post_workload_harness_repair.json"
        and ".env" not in Path(decision_path).parts
        and "gpt55_temporary" not in Path(decision_path).parts
        and all(
            _SHA256_RE.fullmatch(str(admission.get(field) or "")) is not None
            for field in _H0_B_POST_WORKLOAD_REPAIR_FIELDS
            if field.endswith("sha256")
        )
    )
    if not exact:
        raise H0StateGateError("H0-B post-workload replacement admission denied")
    return raw


def _validate_definition(definition: Any, *, candidate_id: str, phase: str) -> None:
    identity = getattr(definition, "identity", None)
    candidate = getattr(definition, "candidate", None)
    namespace = getattr(definition, "embedding_namespace", None)
    guardrail = getattr(definition, "semantic_guardrail", None)
    digest = getattr(definition, "definition_sha256", None)
    if not (
        isinstance(identity, Mapping)
        and identity.get("candidate_id") == candidate_id
        and identity.get("phase") == phase
        and getattr(candidate, "candidate_id", None) == candidate_id
        and isinstance(namespace, Mapping)
        and namespace.get("served_model_id") == "qwen3-embedding-0.6b"
        and namespace.get("dimension") == 1024
        and namespace.get("normalization") == "l2"
        and isinstance(guardrail, Mapping)
        and isinstance(digest, str)
        and len(digest) == 64
    ):
        raise H0ManifestError("authorized full-history runtime definition is invalid")


def _default_prior_completion_validator(
    *,
    root: Path,
    authorization: Mapping[str, Any],
    candidate_id: str,
    phase: str,
    definition: Any,
) -> dict[str, Any]:
    reference = authorization.get("prior_phase_completion")
    if not isinstance(reference, Mapping):
        raise H0ManifestError("H0 authorization lacks prior-phase completion")
    arguments = {
        "root": root,
        "stage_attempt_id": reference.get("stage_attempt_id"),
        "checkpoint_index_path": reference.get("checkpoint_index_path"),
        "checkpoint_index_sha256": reference.get("checkpoint_index_sha256"),
        "candidate_id": candidate_id,
        "runtime_definition_sha256": reference.get("runtime_definition_sha256"),
    }
    if candidate_id == "Q1" and phase == "H0-B":
        return validate_h0_prior_phase_terminal_completion(
            **arguments,
            phase="H0-A",
        )
    if candidate_id == "Q1" and phase == "H0-C":
        return validate_h0_b_terminal_completion(**arguments)
    raise H0ManifestError("prior completion validator is not bound for this candidate")


def _same_authorization(
    expected: Mapping[str, Any],
    checker: Callable[..., Any],
    *,
    state_path: str | Path,
    candidate_id: str,
    phase: str,
) -> Callable[[], Any]:
    frozen = deepcopy(dict(expected))

    def recheck() -> dict[str, Any]:
        observed = checker(
            state_path=state_path,
            candidate_id=candidate_id,
            phase=phase,
        )
        if not isinstance(observed, Mapping) or dict(observed) != frozen:
            raise H0StateGateError("H0 full-history authorization changed")
        return deepcopy(frozen)

    return recheck


async def _assert_graph_empty(graph: Any) -> None:
    from live_runtime import count_nodes

    if await count_nodes(graph) != 0:
        raise H0ManifestError("H0 fresh history graph is not empty")


def _infrastructure_reason(error: H0InfrastructureError) -> str:
    prefix = str(error).split(":", 1)[0]
    if prefix not in {"vllm_unreachable", "embedding_unreachable", "neo4j_unreachable"}:
        return "vllm_unreachable"
    return prefix


def _failure_code(error: BaseException) -> str:
    if isinstance(error, H0StateGateError):
        return "authorization_revoked"
    if isinstance(error, H0QualificationError):
        return "candidate_qualification_failure"
    if isinstance(error, H0ManifestError):
        return "manifest_contract_failure"
    return "candidate_execution_failure"


def _print_progress(event: Mapping[str, Any]) -> None:
    print(json.dumps(dict(event), ensure_ascii=True, sort_keys=True), flush=True)


async def execute_h0_full_history_live(
    *,
    root: str | Path = ROOT,
    state_path: str | Path = DEFAULT_STATE_PATH,
    artifacts_root: str | Path = DEFAULT_ARTIFACTS_ROOT,
    stage_attempt_id: str,
    candidate_id: str,
    phase: str,
    authorization_checker: Callable[..., Any] = authorize_h0_live_entry,
    runtime_definition_loader: Callable[..., Any] = _load_runtime_definition,
    prior_completion_validator: Callable[..., Any] = _default_prior_completion_validator,
    checkpoint_store_factory: Callable[..., H0CheckpointStore] = H0CheckpointStore,
    credential_loader: Callable[[], Mapping[str, Any]] | None = None,
    readiness_runner: Callable[..., Any] = run_h0_stage_readiness,
    corpus_loader: Callable[[Path], Any] = _load_h0_corpus,
    history_factory_builder: Callable[..., Any] = H0GraphitiHistoryFactory,
    full_history_runner: Callable[..., Any] = run_full_history,
    phase_runner: Callable[..., Any] = run_h0_full_history_phase,
    progress_sink: Callable[[dict[str, Any]], Any] = _print_progress,
) -> dict[str, Any]:
    """Execute one explicitly authorized whole H0-B or H0-C stage."""

    if candidate_id not in {"Q1", "Q2", "Q3"} or phase not in {"H0-B", "H0-C"}:
        raise H0ManifestError("full-history candidate or phase is invalid")
    root_path = Path(root).resolve()

    # This must be the first externally supplied call.  In particular, no
    # manifest, prior result, checkpoint directory, dataset, env, or client is
    # touched before it.
    authorization = authorization_checker(
        state_path=state_path,
        candidate_id=candidate_id,
        phase=phase,
    )
    if not isinstance(authorization, Mapping):
        raise H0ManifestError("H0 authorization did not return manifest bindings")
    authorization = deepcopy(dict(authorization))
    repair_admission = _extract_h0_b_harness_repair_admission(
        authorization,
        stage_attempt_id=stage_attempt_id,
        candidate_id=candidate_id,
        phase=phase,
    )
    infrastructure_rerun_admission = _extract_h0_b_infrastructure_rerun_admission(
        authorization,
        stage_attempt_id=stage_attempt_id,
        repair_admission=repair_admission or {},
    )
    post_workload_repair_admission = _extract_h0_b_post_workload_repair_admission(
        authorization,
        stage_attempt_id=stage_attempt_id,
        repair_admission=repair_admission or {},
        infrastructure_rerun_admission=infrastructure_rerun_admission,
    )
    definition = runtime_definition_loader(authorization, root=root_path)
    _validate_definition(definition, candidate_id=candidate_id, phase=phase)
    prior_completion = prior_completion_validator(
        root=root_path,
        authorization=authorization,
        candidate_id=candidate_id,
        phase=phase,
        definition=definition,
    )
    if not isinstance(prior_completion, Mapping) or prior_completion.get("qualified") is not True:
        raise H0ManifestError("prior H0 phase has no qualified terminal completion")

    store = checkpoint_store_factory(
        root=Path(artifacts_root),
        stage_attempt_id=stage_attempt_id,
        candidate_id=candidate_id,
        phase=phase,
        progress_sink=progress_sink,
        repair_admission=repair_admission,
        infrastructure_rerun_admission=infrastructure_rerun_admission,
        post_workload_repair_admission=post_workload_repair_admission,
    )
    store.record_segment(
        "prior_phase_completion",
        "qualified",
        dict(prior_completion),
    )
    if credential_loader is None:
        credential_loader = H0ProjectCredentialLoader(
            root=root_path,
            definition=definition,
        )
    credentials = _CachedAllCredentials(credential_loader)
    ledger: H0AttemptLedger | None = None
    history_factory: Any | None = None
    readiness: Mapping[str, Any] | None = None
    prior_sha256 = canonical_json_sha256(prior_completion)
    failure_stage = "credential_load"

    def record_preworkload_progress(
        stage: str, *, question_id: str | None = None
    ) -> None:
        store.record_segment(
            "preworkload_progress",
            stage,
            {
                "schema_version": "membind.h0.preworkload-progress.v1",
                "protocol_version": "current-validation-v1.3",
                "stage_attempt_id": stage_attempt_id,
                "candidate_id": candidate_id,
                "phase": phase,
                "stage": stage,
                "question_id": question_id,
                "generation_request_count": 0,
                "embedding_request_count": 0,
                "secrets_persisted": False,
            },
        )

    try:
        sections = credentials()
        failure_stage = "stage_readiness"
        readiness_sink = H0StageReadinessCheckpointSink(store)
        readiness = await readiness_runner(
            state_path=state_path,
            stage_attempt_id=stage_attempt_id,
            candidate_id=candidate_id,
            phase=phase,
            construction_credential_loader=lambda: deepcopy(
                sections["construction"]
            ),
            resolved_identity_loader=lambda observed: (
                deepcopy(definition.identity)
                if isinstance(observed, Mapping) and dict(observed) == authorization
                else (_ for _ in ()).throw(
                    H0StateGateError("H0 readiness authorization changed")
                )
            ),
            embedding_binding={
                "base_url": H0_EMBEDDING_BASE_URL,
                "served_model_id": definition.embedding_namespace["served_model_id"],
                "vllm_version": H0_EMBEDDING_VLLM_VERSION,
                "dimension": definition.embedding_namespace["dimension"],
                "normalization": definition.embedding_namespace["normalization"],
            },
            embedding_credentials=sections["embedding"],
            neo4j_binding={
                "uri": H0_NEO4J_URI,
                "user": sections["neo4j"]["user"],
            },
            neo4j_credentials={
                "uri": sections["neo4j"]["uri"],
                "user": sections["neo4j"]["user"],
                "password": sections["neo4j"]["password"],
            },
            progress_sink=readiness_sink,
            authorization_checker=authorization_checker,
        )
        if not isinstance(readiness, Mapping) or not (
            readiness.get("status") == "ready"
            and readiness.get("construction_readiness_count") == 1
            and readiness.get("embedding_readiness_count") == 1
            and readiness.get("neo4j_readiness_count") == 1
            and readiness.get("authorization_recheck_count") == 1
            and readiness.get("generation_requests") == 0
            and readiness.get("embedding_request_count") == 0
            and readiness.get("per_history_warmup_count") == 0
        ):
            raise H0ManifestError("H0 full-stack readiness did not qualify")
        recheck = _same_authorization(
            authorization,
            authorization_checker,
            state_path=state_path,
            candidate_id=candidate_id,
            phase=phase,
        )
        recheck()
        store.record_segment("stage_readiness_result", "ready", dict(readiness))

        failure_stage = "corpus_load"
        corpus = corpus_loader(root_path)
        record_preworkload_progress("corpus_ready")
        ledger = H0AttemptLedger(stage_attempt_id=stage_attempt_id)
        semantic_collector = H0SemanticEvidenceCollector()
        failure_stage = "history_factory_construction"
        history_factory = history_factory_builder(
            definition=definition,
            credentials=sections,
            ledger=ledger,
            semantic_collector=semantic_collector,
            authorization_rechecker=recheck,
        )
        record_preworkload_progress("history_factory_ready")

        async def run_history(*, item: Any, stage_attempt_id: str, phase_name: str) -> dict[str, Any]:
            nonlocal failure_stage
            if phase_name != phase or stage_attempt_id != ledger.stage_attempt_id:
                raise H0ManifestError("H0 history runner stage identity mismatch")

            async def gated_graph_factory() -> Any:
                nonlocal failure_stage
                failure_stage = "graph_construction"
                recheck()
                record_preworkload_progress(
                    "graph_construction_started", question_id=item.question_id
                )
                graph = await _maybe_await(history_factory())
                record_preworkload_progress(
                    "graph_construction_ready", question_id=item.question_id
                )
                failure_stage = "history_workload"
                return graph

            async def checkpoint_source(event: Mapping[str, Any]) -> None:
                sequence = event.get("source_sequence")
                if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                    raise H0ManifestError("H0 source checkpoint sequence is invalid")
                store.record_segment(
                    "source_sequence",
                    f"{item.question_id}-{sequence:03d}",
                    {
                        "phase_checkpoint": dict(event),
                        "attempt_ledger": ledger.safe_artifact(),
                        "runtime_evidence": history_factory.safe_runtime_evidence(),
                        "runtime_definition_sha256": definition.definition_sha256,
                        "prior_phase_completion_sha256": prior_sha256,
                    },
                )

            from graphiti_native import add_episode
            from live_outputs import export_canonical_graph
            from live_runtime import clear_database

            result = await full_history_runner(
                instance=item.instance,
                episodes=item.episodes,
                stage_attempt_id=stage_attempt_id,
                graph_factory=gated_graph_factory,
                clear_graph=clear_database,
                assert_graph_empty=_assert_graph_empty,
                close_graph=close_h0_graphiti_history,
                ingest_episode=add_episode,
                export_graph=export_canonical_graph,
                evaluate_retrieval=evaluate_h0_retrieval,
                source_checkpoint=checkpoint_source,
                semantic_collector=semantic_collector,
                semantic_guardrail=definition.semantic_guardrail,
                ledger=ledger,
            )
            if not isinstance(result, Mapping):
                raise H0ManifestError("H0 history result is invalid")
            result = deepcopy(dict(result))
            store.record_segment(
                "history_result",
                item.question_id,
                {
                    "history_result": result,
                    "history_result_sha256": canonical_json_sha256(result),
                    "attempt_ledger": ledger.safe_artifact(),
                    "runtime_evidence": history_factory.safe_runtime_evidence(),
                    "runtime_definition_sha256": definition.definition_sha256,
                },
            )
            return result

        failure_stage = "phase_execution"
        phase_result = await phase_runner(
            corpus=corpus,
            phase_name=phase,
            stage_attempt_id=stage_attempt_id,
            history_runner=run_history,
            semantic_guardrail=definition.semantic_guardrail,
        )
        if not isinstance(phase_result, Mapping) or phase_result.get("qualified") is not True:
            raise H0QualificationError("H0 full-history phase did not qualify")
        phase_result = deepcopy(dict(phase_result))
        terminal_hash = canonical_json_sha256(phase_result)
        store.record_segment(
            "stage_result",
            "qualified",
            {
                "phase_result": phase_result,
                "phase_result_sha256": terminal_hash,
                "attempt_ledger": ledger.safe_artifact(),
                "runtime_evidence": history_factory.safe_runtime_evidence(),
                "runtime_definition_sha256": definition.definition_sha256,
                "prior_phase_completion_sha256": prior_sha256,
                "stage_readiness_sha256": canonical_json_sha256(readiness),
            },
        )
        terminal = store.mark_stage_complete(terminal_hash)
        return {
            "schema_version": "membind.h0.full-history-live-run-result.v1",
            "protocol_version": "current-validation-v1.3",
            "candidate_id": candidate_id,
            "phase": phase,
            "stage_attempt_id": stage_attempt_id,
            "status": terminal["status"],
            "phase_result": phase_result,
            "checkpoint_index_path": terminal["checkpoint_index_path"],
            "checkpoint_index_sha256": terminal["checkpoint_index_sha256"],
            "runtime_definition_sha256": definition.definition_sha256,
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }
    except H0InfrastructureError as exc:
        if store.index.get("status") == "running":
            reason = _infrastructure_reason(exc)
            store.record_segment(
                "infrastructure_failure",
                reason.replace("_", "-"),
                {
                    "failure_code": reason,
                    "attempt_ledger": ledger.safe_artifact() if ledger else {
                        "schema_version": "membind.h0.attempt-ledger.v1",
                        "stage_attempt_id": stage_attempt_id,
                        "logical_trials": [],
                        "http_attempts": [],
                        "secrets_persisted": False,
                        "raw_prompts_persisted": False,
                        "raw_responses_persisted": False,
                    },
                    "runtime_evidence": (
                        history_factory.safe_runtime_evidence()
                        if history_factory is not None
                        else {"fresh_graph_count": 0, "histories": []}
                    ),
                    "runtime_definition_sha256": definition.definition_sha256,
                    "failure_stage": failure_stage,
                    "candidate_advance_allowed": False,
                    "partial_qualification_reusable": False,
                },
            )
            store.mark_infrastructure_interruption(reason)
        raise
    except Exception as exc:
        if store.index.get("status") == "running":
            code = _failure_code(exc)
            failure = {
                "failure_code": code,
                "attempt_ledger": ledger.safe_artifact() if ledger else {
                    "schema_version": "membind.h0.attempt-ledger.v1",
                    "stage_attempt_id": stage_attempt_id,
                    "logical_trials": [],
                    "http_attempts": [],
                    "secrets_persisted": False,
                    "raw_prompts_persisted": False,
                    "raw_responses_persisted": False,
                },
                "runtime_evidence": (
                    history_factory.safe_runtime_evidence()
                    if history_factory is not None
                    else {"fresh_graph_count": 0, "histories": []}
                ),
                "runtime_definition_sha256": definition.definition_sha256,
                "failure_stage": failure_stage,
                "candidate_advance_allowed": False,
            }
            store.record_segment("candidate_failure", code, failure)
            store.mark_candidate_failure(code, canonical_json_sha256(failure))
        raise
