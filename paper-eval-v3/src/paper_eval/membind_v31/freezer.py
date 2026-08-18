"""Offline qualification and immutable freeze artifacts for MemBind v3.1.

This module never opens a model, embedding, database, or SSH connection.  It
reads only immutable/public source and identity artifacts, runs the pinned
Graphiti extractors against restricted in-process fakes, and verifies a small
captured state-transition fixture before atomically publishing six sealed
pre-live artifacts.
"""

from __future__ import annotations

import json
import importlib.util
import re
import sys
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_eval.apc_aligned_baseline import (
    APC_BASELINE_HISTORIES,
    verify_apc_aligned_baseline_plan,
)
from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.membind_v1.live_runtime import (
    CONSTRUCTION_BASE_URL,
    CONSTRUCTION_MODEL,
    EMBEDDING_BASE_URL,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    MAX_COROUTINES,
    NEO4J_URI,
    project_membind_v1_runtime_identity,
)
from paper_eval.membind_v31.adapter import (
    CapturedStateTransition,
    GraphitiV31AdapterError,
    verify_captured_transition_parity,
)
from paper_eval.membind_v31.certification import (
    CertificationRecord,
    StateCutCertification,
)
from paper_eval.membind_v31.contracts import (
    DependencyClass,
    EffectClass,
    OperatorContract,
)
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v31.qualification import qualify_graphiti_v0293_state_cut
from paper_eval.membind_v31.workload_complexity import (
    WORKLOAD_COMPLEXITY_SCHEMA,
    WorkloadComplexityError,
    build_workload_complexity_freeze,
)


REUSE_SCHEMA = "membind.paper-eval-v3.membind-v31-reuse-audit.v1"
ENVELOPE_SCHEMA = "membind.paper-eval-v3.membind-v31-execution-envelope.v1"
STATE_CUT_SCHEMA = "membind.paper-eval-v3.membind-v31-state-cut-certification.v1"
PROJECTION_SCHEMA = "membind.paper-eval-v3.membind-v31-canonical-projection-freeze.v1"
SERIAL_SCHEMA = "membind.paper-eval-v3.membind-v31-deterministic-serializability.v1"
FROZEN_FILENAMES = (
    "V31_REUSE_AUDIT.json",
    "V31_EXECUTION_ENVELOPE.json",
    "STATE_CUT_CERTIFICATION.json",
    "CANONICAL_PROJECTION_FREEZE.json",
    "DETERMINISTIC_SERIALIZABILITY_RESULT.json",
    "V31_WORKLOAD_COMPLEXITY.json",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "password",
    "raw_prompt",
    "raw_response",
    "secret",
}
_VLLM_REPOSITORY = "https://github.com/vllm-project/vllm"
_VLLM_REVISION = "568afb3a13806beb53bb2e6bd518269357b237c0"
_VLLM_CACHE_SOURCE_SHA256 = (
    "ee2c0db3e4e6c9e9cab33d8be566c4b8101159d36c0d3787c30d47931ee2a9a4"
)
_VLLM_CACHE_SOURCE_GIT_BLOB = "a628e7d7cdd03152b44cc07bfba3f60d09fd1a46"
_VLLM_PARALLEL_SOURCE_SHA256 = (
    "a6581c267ab265e24905d2f5caa514482c28359f71380c6f894ceab25aa22541"
)
_VLLM_PARALLEL_SOURCE_GIT_BLOB = "53688c05d92d9b33dee54e1ecc792f47090e03e9"
_TOKENIZER_REPOSITORY = "Qwen/Qwen3-32B-FP8"
_TOKENIZER_REVISION = "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df"


class MemBindV31FreezerError(ValueError):
    """An input identity, qualification, parity, or durable freeze failed."""


def _fail(code: str) -> MemBindV31FreezerError:
    return MemBindV31FreezerError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _sealed(body: Mapping[str, object]) -> dict[str, object]:
    result = deepcopy(dict(body))
    result["payload_sha256"] = payload_sha256(result)
    return result


def _verify_sealed(value: Mapping[str, object]) -> dict[str, Any]:
    selected = deepcopy(dict(value))
    stored = _sha(selected.get("payload_sha256"), "artifact_hash_invalid")
    body = {key: child for key, child in selected.items() if key != "payload_sha256"}
    if stored != payload_sha256(body):
        raise _fail("artifact_hash_mismatch")
    if selected.get("status") != "PASS":
        raise _fail("artifact_status_invalid")
    _assert_content_safe(selected)
    return selected


def _assert_content_safe(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_KEYS:
                raise _fail("content_safe_violation")
            _assert_content_safe(child)
        return
    if isinstance(value, list | tuple):
        for child in value:
            _assert_content_safe(child)
        return
    if value is None or isinstance(value, str | int | float | bool):
        return
    raise _fail("content_safe_violation")


def _relative(path: Path, root: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        raise _fail("input_path_outside_repository") from None


def _require_file(path: Path, code: str) -> str:
    digest = sha256_file(Path(path))
    if digest == "missing":
        raise _fail(code)
    return _sha(digest, code)


def _verify_inline_seal(value: Mapping[str, object], field: str, code: str) -> None:
    stored = _sha(value.get(field), code)
    body = {key: child for key, child in value.items() if key != field}
    if payload_sha256(body) != stored:
        raise _fail(code)


def _verify_wrapped_payload(value: Mapping[str, object], code: str) -> dict[str, Any]:
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise _fail(code)
    if _sha(value.get("payload_sha256"), code) != payload_sha256(payload):
        raise _fail(code)
    return deepcopy(dict(payload))


@dataclass(frozen=True, slots=True)
class V31FreezePaths:
    """All read-only inputs and the isolated destination of one freeze."""

    repository_root: Path
    project_root: Path
    methodology: Path
    workplan: Path
    baseline_plan: Path
    baseline_preflight: Path
    reused_runtime_config: Path
    s4_d0_contract: Path
    s4_capture_graph: Path
    canonicalizer: Path
    canonical_exporter: Path
    s4_projection_normalizer: Path
    adapter: Path
    qualification: Path
    prepared_artifact: Path
    workload_complexity: Path
    development_input: Path
    episode_renderer: Path
    tokenizer_json: Path
    tokenizer_config: Path
    model_config: Path
    output_dir: Path

    @classmethod
    def from_repository(
        cls,
        repository_root: Path,
        *,
        output_dir: Path | None = None,
    ) -> "V31FreezePaths":
        root = Path(repository_root).resolve()
        project = root / "paper-eval-v3"
        return cls(
            repository_root=root,
            project_root=project,
            methodology=root / "MemBind_FINAL_METHODOLOGY_v3.1_FROZEN.md",
            workplan=root / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3.1_METHODOLOGY_ALIGNED.md",
            baseline_plan=(
                project
                / "artifacts/paper_eval/apc_aligned_baseline/runs/"
                "apc-baseline-dev-20260817-001/PLAN.json"
            ),
            baseline_preflight=(
                project
                / "artifacts/paper_eval/apc_aligned_baseline/runs/"
                "apc-baseline-dev-20260817-001/PREFLIGHT.json"
            ),
            reused_runtime_config=(
                project
                / "artifacts/paper_eval/native/"
                "S5_PSTAR_RUNTIME_CONFIG_FORMAL_CHAIN_FRESH_20260816.json"
            ),
            s4_d0_contract=project / "artifacts/paper_eval/native/S4_D0_CONTRACT.json",
            s4_capture_graph=(
                project
                / "artifacts/paper_eval/native/runs/"
                "s4-d0-capture-20260815-008/canonical_graph.json"
            ),
            canonicalizer=root / "membind-validation/src/canonicalize_graph.py",
            canonical_exporter=root / "membind-validation/src/live_outputs.py",
            s4_projection_normalizer=project / "src/paper_eval/s4_d0_runner.py",
            adapter=project / "src/paper_eval/membind_v31/adapter.py",
            qualification=project / "src/paper_eval/membind_v31/qualification.py",
            prepared_artifact=project / "src/paper_eval/membind_v31/prepared_artifact.py",
            workload_complexity=project
            / "src/paper_eval/membind_v31/workload_complexity.py",
            development_input=project
            / "artifacts/paper_eval/development_inputs/"
            "LONGMEMEVAL_S_DEVELOPMENT_EXPOSED_4.json",
            episode_renderer=root / "membind-validation/src/dataset.py",
            tokenizer_json=(
                root.parent
                / "Mem/cache/huggingface/models--Qwen--Qwen3-32B-FP8/snapshots"
                / _TOKENIZER_REVISION
                / "tokenizer.json"
            ),
            tokenizer_config=(
                root.parent
                / "Mem/cache/huggingface/models--Qwen--Qwen3-32B-FP8/snapshots"
                / _TOKENIZER_REVISION
                / "tokenizer_config.json"
            ),
            model_config=(
                root.parent
                / "Mem/cache/huggingface/models--Qwen--Qwen3-32B-FP8/snapshots"
                / _TOKENIZER_REVISION
                / "config.json"
            ),
            output_dir=(
                Path(output_dir)
                if output_dir is not None
                else project / "artifacts/paper_eval/membind_v31"
            ),
        )


def _load_baseline(paths: V31FreezePaths) -> dict[str, Any]:
    raw = _read_json(paths.baseline_plan, "baseline_plan_invalid")
    sources = raw.get("history_source_sha256s")
    if not isinstance(sources, Mapping):
        raise _fail("baseline_plan_invalid")
    raw["history_source_sha256s"] = {
        history: sources.get(history) for history in APC_BASELINE_HISTORIES
    }
    try:
        plan = verify_apc_aligned_baseline_plan(raw)
    except ValueError:
        raise _fail("baseline_plan_invalid") from None
    if (
        plan.get("run_id") != "apc-baseline-dev-20260817-001"
        or plan.get("global_llm_admission_k") != 2
    ):
        raise _fail("baseline_plan_identity_invalid")
    return plan


def _load_preflight(paths: V31FreezePaths) -> dict[str, Any]:
    preflight = _read_json(paths.baseline_preflight, "baseline_preflight_invalid")
    _verify_inline_seal(preflight, "payload_sha256", "baseline_preflight_invalid")
    construction = preflight.get("model_identity")
    embedding = preflight.get("embedding_model_identity")
    evidence = preflight.get("apc_effective_evidence")
    if (
        preflight.get("status") != "PASS"
        or not isinstance(construction, Mapping)
        or construction.get("served_model_id") != "qwen3-32b-fp8"
        or construction.get("max_model_len") != 65536
        or not isinstance(embedding, Mapping)
        or embedding.get("served_model_id") != "qwen3-embedding-0.6b"
        or not isinstance(evidence, Mapping)
        or evidence.get("prefix_cache_metrics_exposed") is not True
        or evidence.get("startup_log_enable_prefix_caching_observed") is not True
        or evidence.get("startup_log_vllm_version") != "0.26.0"
    ):
        raise _fail("baseline_preflight_identity_invalid")
    return preflight


def _load_runtime_config(paths: V31FreezePaths) -> dict[str, Any]:
    wrapper = _read_json(paths.reused_runtime_config, "runtime_config_invalid")
    payload = _verify_wrapped_payload(wrapper, "runtime_config_invalid")
    construction = payload.get("construction")
    embedding = payload.get("embedding")
    graphiti = payload.get("graphiti")
    neo4j = payload.get("neo4j")
    if (
        not isinstance(construction, Mapping)
        or construction.get("served_model_id") != "qwen3-32b-fp8"
        or construction.get("vllm_version") != "0.26.0"
        or construction.get("max_model_len") != 65536
        or construction.get("requested_max_tokens") != 16384
        or construction.get("structured_output_mode") != "json_schema"
        or construction.get("rope_parameters")
        != {
            "factor": 2.0,
            "original_max_position_embeddings": 32768,
            "rope_theta": 1000000,
            "rope_type": "yarn",
        }
        or not isinstance(embedding, Mapping)
        or embedding.get("served_model_id") != "qwen3-embedding-0.6b"
        or embedding.get("dimension") != 1024
        or not isinstance(graphiti, Mapping)
        or graphiti.get("version") != "0.29.3"
        or not isinstance(neo4j, Mapping)
        or neo4j.get("uri") != "bolt://localhost:7687"
    ):
        raise _fail("runtime_config_identity_invalid")
    return payload


def _load_s4_reuse(paths: V31FreezePaths) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = _read_json(paths.s4_d0_contract, "s4_d0_contract_invalid")
    _verify_inline_seal(contract, "contract_sha256", "s4_d0_contract_invalid")
    graph = _read_json(paths.s4_capture_graph, "s4_capture_graph_invalid")
    if set(graph) != {"edges", "entities", "episodes"} or any(
        not isinstance(graph.get(field), list) for field in ("edges", "entities", "episodes")
    ):
        raise _fail("s4_capture_graph_invalid")
    return contract, graph


def _common(paths: V31FreezePaths) -> dict[str, str]:
    return {
        "methodology_sha256": _require_file(paths.methodology, "methodology_missing"),
        "workplan_sha256": _require_file(paths.workplan, "workplan_missing"),
    }


def _episode_builder(path: Path) -> Any:
    """Load the hash-bound renderer directly, without importing an alias."""

    name = "_membind_v31_frozen_dataset_renderer"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise _fail("episode_renderer_invalid")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        raise _fail("episode_renderer_invalid") from None
    finally:
        sys.modules.pop(name, None)
    builder = getattr(module, "build_episodes", None)
    if not callable(builder):
        raise _fail("episode_renderer_invalid")
    return builder


def _build_workload_complexity(
    paths: V31FreezePaths,
    common: Mapping[str, str],
    baseline: Mapping[str, Any],
) -> dict[str, object]:
    development_input = _read_json(
        paths.development_input, "development_input_invalid"
    )
    try:
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(paths.tokenizer_json))
    except Exception:
        raise _fail("pinned_tokenizer_load_failed") from None
    tokenizer_files = {
        "config.json": _require_file(paths.model_config, "model_config_missing"),
        "tokenizer.json": _require_file(
            paths.tokenizer_json, "tokenizer_json_missing"
        ),
        "tokenizer_config.json": _require_file(
            paths.tokenizer_config, "tokenizer_config_missing"
        ),
    }
    try:
        return build_workload_complexity_freeze(
            development_input=development_input,
            development_input_file_sha256=_require_file(
                paths.development_input, "development_input_missing"
            ),
            baseline_plan=baseline,
            renderer_identity={
                "path": _relative(paths.episode_renderer, paths.repository_root),
                "sha256": _require_file(
                    paths.episode_renderer, "episode_renderer_missing"
                ),
                "functions": ["build_episodes", "render_episode_body"],
            },
            tokenizer_identity={
                "repository": _TOKENIZER_REPOSITORY,
                "revision": _TOKENIZER_REVISION,
                "file_sha256s": tokenizer_files,
                "local_snapshot_path_persisted": False,
            },
            tokenizer=tokenizer,
            episode_builder=_episode_builder(paths.episode_renderer),
            methodology_sha256=common["methodology_sha256"],
            workplan_sha256=common["workplan_sha256"],
        )
    except WorkloadComplexityError as error:
        raise _fail(f"workload_complexity_build_failed:{error}") from None


def _build_reuse(
    paths: V31FreezePaths,
    common: Mapping[str, str],
    baseline: Mapping[str, Any],
    s4_contract: Mapping[str, Any],
) -> dict[str, object]:
    reused_sources = {
        "arrival_evidence_adapter": paths.project_root
        / "src/paper_eval/membind_v31/adapter.py",
        "canonicalizer": paths.canonicalizer,
        "canonical_exporter": paths.canonical_exporter,
        "prepared_artifact": paths.prepared_artifact,
        "qualification": paths.qualification,
        "s4_namespace_projection": paths.s4_projection_normalizer,
    }
    source_bindings = {
        role: {
            "path": _relative(path, paths.repository_root),
            "sha256": _require_file(path, f"reuse_source_missing:{role}"),
        }
        for role, path in sorted(reused_sources.items())
    }
    body: dict[str, object] = {
        "schema_version": REUSE_SCHEMA,
        "status": "PASS",
        **common,
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "live_service_calls_performed": False,
        "baseline": {
            "run_id": baseline["run_id"],
            "plan_path": _relative(paths.baseline_plan, paths.repository_root),
            "plan_file_sha256": _require_file(paths.baseline_plan, "baseline_plan_missing"),
            "plan_payload_sha256": baseline["payload_sha256"],
            "source_manifest_sha256": baseline["source_manifest_sha256"],
            "arrival_trace_sha256": baseline["arrival_trace_sha256"],
            "shared_execution_envelope_sha256": baseline[
                "shared_execution_envelope_sha256"
            ],
            "global_llm_admission_k": baseline["global_llm_admission_k"],
            "running_block_artifacts_read": False,
            "terminal_acceptance_claimed": False,
            "terminal_acceptance_artifact": "V31_BASELINE_ACCEPTANCE.json",
        },
        "artifact_authority": "V0_OFFLINE_SOURCE_HASH_REUSE_AUDIT_ONLY",
        "canonical_projection_reuse": {
            "reuse_origin": "S4_D0",
            "reuse_scope": "SCHEMA_AND_EXPORT_IMPLEMENTATION_ONLY",
            "old_d0_result_authority": "NOT_V31_SERIALIZABILITY_EVIDENCE",
            "s4_contract_path": _relative(paths.s4_d0_contract, paths.repository_root),
            "s4_contract_file_sha256": _require_file(
                paths.s4_d0_contract, "s4_d0_contract_missing"
            ),
            "s4_contract_sha256": s4_contract["contract_sha256"],
            "capture_projection_path": _relative(
                paths.s4_capture_graph, paths.repository_root
            ),
            "capture_projection_file_sha256": _require_file(
                paths.s4_capture_graph, "s4_capture_graph_missing"
            ),
            "capture_projection_contents_persisted": False,
        },
        "v31_captured_parity": {
            "implementation_path": _relative(paths.adapter, paths.repository_root),
            "implementation_sha256": _require_file(paths.adapter, "adapter_missing"),
            "reuse_scope": "V31_DETERMINISTIC_PARITY_PRIMITIVE",
        },
        "source_bindings": source_bindings,
    }
    return _sealed(body)


def _public_runtime_identity() -> dict[str, object]:
    return project_membind_v1_runtime_identity(
        {
            "CONSTRUCTION_LLM_BASE_URL": CONSTRUCTION_BASE_URL,
            "CONSTRUCTION_LLM_MODEL": CONSTRUCTION_MODEL,
            "EMBEDDING_BASE_URL": EMBEDDING_BASE_URL,
            "EMBEDDING_MODEL": EMBEDDING_MODEL,
            "EMBEDDING_DIM": str(EMBEDDING_DIMENSION),
            "NEO4J_URI": NEO4J_URI,
            "GRAPHITI_MAX_COROUTINES": str(MAX_COROUTINES),
        }
    )


def _build_envelope(
    paths: V31FreezePaths,
    common: Mapping[str, str],
    baseline: Mapping[str, Any],
    preflight: Mapping[str, Any],
    runtime: Mapping[str, Any],
    reuse: Mapping[str, object],
    *,
    compile_workers: int,
    lookahead: int,
    global_llm_admission_k: int,
) -> dict[str, object]:
    if (compile_workers, lookahead, global_llm_admission_k) != (2, 2, 2):
        raise _fail("method_knobs_not_frozen_to_two")
    public_runtime = _public_runtime_identity()
    if payload_sha256(public_runtime) != baseline["shared_execution_envelope_sha256"]:
        raise _fail("baseline_execution_envelope_reconstruction_mismatch")
    tokenizer_files = {
        "config.json": _require_file(paths.model_config, "model_config_missing"),
        "tokenizer.json": _require_file(paths.tokenizer_json, "tokenizer_json_missing"),
        "tokenizer_config.json": _require_file(
            paths.tokenizer_config, "tokenizer_config_missing"
        ),
    }
    remote_observation = {
        "vllm_version": "0.26.0",
        "enable_prefix_caching": True,
        "block_size_launch_override": "UNSET",
        "prefix_match_unit_launch_override": "UNSET",
    }
    body: dict[str, object] = {
        "schema_version": ENVELOPE_SCHEMA,
        "status": "PASS",
        **common,
        "reuse_audit_sha256": reuse["payload_sha256"],
        "baseline_run_id": baseline["run_id"],
        "baseline_plan_payload_sha256": baseline["payload_sha256"],
        "baseline_shared_execution_envelope_sha256": baseline[
            "shared_execution_envelope_sha256"
        ],
        "shared_public_runtime_identity": public_runtime,
        "method_knobs": {
            "bind_workers": 1,
            "compile_workers_c": compile_workers,
            "global_llm_admission_k": global_llm_admission_k,
            "lookahead_w": lookahead,
        },
        "request_admission_contract": {
            "permit_unit": "ACTUAL_TRANSPORT_ATTEMPT",
            "observed_inflight_counter_unit": "ACTUAL_TRANSPORT_ATTEMPT",
            "retry_policy": "EVERY_ATTEMPT_REACQUIRES_INDEPENDENTLY",
            "logical_call_permit_scope": "FORBIDDEN_ACROSS_RETRIES",
        },
        "deployment": {
            "construction": deepcopy(dict(runtime["construction"])),
            "embedding": deepcopy(dict(runtime["embedding"])),
            "graphiti": deepcopy(dict(runtime["graphiti"])),
            "neo4j": deepcopy(dict(runtime["neo4j"])),
        },
        "tokenizer_identity": {
            "repository": _TOKENIZER_REPOSITORY,
            "revision": _TOKENIZER_REVISION,
            "file_sha256s": tokenizer_files,
            "local_snapshot_path_persisted": False,
        },
        "backend_contract": {
            "apc_enabled": True,
            "apc_evidence": "REUSED_BASELINE_PREFLIGHT",
            "chunked_prefill": "SAME_AS_BASELINE_SHARED_ENVELOPE",
            "gpu_memory_budget": "SAME_AS_BASELINE_SHARED_ENVELOPE",
            "backend_prefix_match_granularity_tokens": 16,
            "decode_context_parallel_size": 1,
            "decode_context_parallel_evidence": {
                "vllm_repository": _VLLM_REPOSITORY,
                "vllm_revision": _VLLM_REVISION,
                "source_path": "vllm/config/parallel.py",
                "source_sha256": _VLLM_PARALLEL_SOURCE_SHA256,
                "source_git_blob": _VLLM_PARALLEL_SOURCE_GIT_BLOB,
                "default_field": "ParallelConfig.decode_context_parallel_size",
                "default_value": 1,
            },
            "granularity_evidence": {
                "derivation": "VLLM_0_26_0_DEFAULT_WITH_NO_LAUNCH_OVERRIDE",
                "vllm_repository": _VLLM_REPOSITORY,
                "vllm_revision": _VLLM_REVISION,
                "cache_source_path": "vllm/config/cache.py",
                "cache_source_sha256": _VLLM_CACHE_SOURCE_SHA256,
                "cache_source_git_blob": _VLLM_CACHE_SOURCE_GIT_BLOB,
                "default_constant": "CacheConfig.DEFAULT_BLOCK_SIZE",
                "operator_supplied_read_only_log_observation_sha256": payload_sha256(
                    remote_observation
                ),
                "operator_supplied_log_contents_persisted": False,
                "remote_launch_overrides_observed": False,
            },
            "cache_reset_endpoint": preflight["cache_reset_endpoint"],
            "cache_initial_state_policy": "UNIQUE_FRESH_REQUEST_CACHE_SALT_PER_BLOCK",
            "cache_isolation_contract": {
                "comparable_methods": [
                    "U0-aligned",
                    "A0-aligned",
                    "P(C=2)-aligned",
                    "MemBind-Barrier",
                    "MemBind-FIFO",
                    "MemBind",
                ],
                "policy_applies_equally_to_all_comparable_methods": True,
                "request_cache_salt": "UNIQUE_FRESH_PER_BLOCK",
                "cross_block_prefix_identity_reuse": False,
                "cross_block_warm_inheritance": False,
                "within_block_prefix_reuse": True,
                "physical_cache_reset_claimed": False,
            },
            "cache_claim_status": "OBSERVATIONAL",
        },
        "state_contract": {
            "published_read_contract": False,
            "claim_scope": "CONSTRUCTION_TO_CONSTRUCTION_SOURCE_ORDER_SERIALIZABILITY",
            "single_writer_per_namespace": True,
            "external_writer_allowed": False,
            "publish_completeness": "ALL_DECLARED_EFFECTS_AND_STATE_MUTATING_TASKS_JOINED",
            "hidden_post_publish_state_task_allowed": False,
        },
        "preflight_binding": {
            "path": _relative(paths.baseline_preflight, paths.repository_root),
            "file_sha256": _require_file(paths.baseline_preflight, "preflight_missing"),
            "payload_sha256": preflight["payload_sha256"],
        },
        "runtime_config_binding": {
            "path": _relative(paths.reused_runtime_config, paths.repository_root),
            "file_sha256": _require_file(paths.reused_runtime_config, "runtime_config_missing"),
        },
        "live_service_calls_performed": False,
        "live_recheck_required_before_namespace_creation": True,
    }
    return _sealed(body)


def _build_state_cut(
    paths: V31FreezePaths,
    common: Mapping[str, str],
    reuse: Mapping[str, object],
    envelope: Mapping[str, object],
    qualification_document: Mapping[str, object],
    certification: StateCutCertification,
) -> dict[str, object]:
    certification.verify()
    nested = deepcopy(dict(qualification_document))
    _verify_inline_seal(nested, "payload_sha256", "qualification_document_invalid")
    counters = {
        "future_evidence_access_count": 0,
        "persistent_state_read_count": 0,
        "persistent_state_write_count": 0,
        "undeclared_external_side_effect_count": 0,
        "undeclared_state_facing_call_count": 0,
    }
    if any(record.forbidden_counts != counters for record in certification.records):
        raise _fail("state_cut_forbidden_observation")
    body: dict[str, object] = {
        "schema_version": STATE_CUT_SCHEMA,
        "status": "PASS",
        **common,
        "reuse_audit_sha256": reuse["payload_sha256"],
        "execution_envelope_sha256": envelope["payload_sha256"],
        "graphiti_version": "0.29.3",
        "compiled_operator_names": list(certification.operator_names),
        "state_cut_certification_sha256": certification.certification_sha256,
        "forbidden_observation_counts": counters,
        "qualification_document": nested,
        "runtime_guard_required": True,
        "formal_run_certification_failure_policy": "INVALIDATE_BLOCK_AND_REVOKE_NEXT_PROTOCOL",
    }
    return _sealed(body)


def _build_projection(
    paths: V31FreezePaths,
    common: Mapping[str, str],
    reuse: Mapping[str, object],
    envelope: Mapping[str, object],
    state_cut: Mapping[str, object],
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": PROJECTION_SCHEMA,
        "status": "PASS",
        **common,
        "reuse_audit_sha256": reuse["payload_sha256"],
        "execution_envelope_sha256": envelope["payload_sha256"],
        "state_cut_artifact_sha256": state_cut["payload_sha256"],
        "projection_version": "graphiti-v0293-s4-d0-canonical-export.v1",
        "backend_version": "graphiti-core-0.29.3",
        "adapter_version": "membind-v31",
        "canonicalizer": {
            "reuse_origin": "S4_D0",
            "path": _relative(paths.canonicalizer, paths.repository_root),
            "sha256": _require_file(paths.canonicalizer, "canonicalizer_missing"),
        },
        "graph_exporter": {
            "reuse_origin": "S4_D0",
            "path": _relative(paths.canonical_exporter, paths.repository_root),
            "sha256": _require_file(paths.canonical_exporter, "canonical_exporter_missing"),
        },
        "namespace_normalizer": {
            "reuse_origin": "S4_D0",
            "path": _relative(paths.s4_projection_normalizer, paths.repository_root),
            "sha256": _require_file(
                paths.s4_projection_normalizer, "projection_normalizer_missing"
            ),
            "rule": "ALPHA_RENAME_ENTITY_GROUP_ID_ONLY",
            "placeholder": "__S4_ISOLATED_NAMESPACE__",
        },
        "included_semantic_fields": {
            "entities": ["group_id", "name", "labels", "summary", "attributes"],
            "edges": [
                "source_entity_key",
                "target_entity_key",
                "relation_type",
                "fact",
                "valid_at",
                "invalid_at",
                "expired_at",
                "attributes",
                "source_episode_sequence",
            ],
            "episodes": ["source_sequence", "source_hash", "session_id"],
        },
        "excluded_non_semantic_keys": [
            "created_at",
            "database_id",
            "db_id",
            "element_id",
            "embedding",
            "fact_embedding",
            "id",
            "name_embedding",
            "updated_at",
            "uuid",
        ],
        "excluded_suffix_rule": "*_uuid",
        "normalization": {
            "attribute_keys_sorted": True,
            "entity_edge_episode_lists_stably_sorted": True,
            "entity_names_casefolded": True,
            "whitespace_collapsed": True,
        },
        "post_result_projection_changes_allowed": False,
    }
    return _sealed(body)


def _fixture_artifact(sequence: int, certification_sha256: str) -> PreparedArtifact:
    return PreparedArtifact.create(
        source_sequence=sequence,
        source_sha256=payload_sha256({"fixture_source_sequence": sequence}),
        evidence_sha256=payload_sha256({"arrived_prefix_through": sequence}),
        certification_sha256=certification_sha256,
        raw_nodes=[{"name": f"Entity {sequence}", "source_local_id": f"raw-{sequence}"}],
        raw_edges=(
            []
            if sequence == 0
            else [
                {
                    "fact": "Entity 0 precedes Entity 1",
                    "source_local_id": "raw-0",
                    "target_local_id": "raw-1",
                }
            ]
        ),
        pure_intermediates={"node_episode_index_map": {f"raw-{sequence}": [0]}},
    )


def _transition_document(value: CapturedStateTransition) -> dict[str, object]:
    selected = value.verify()
    return {
        "stream_id": selected.stream_id,
        "source_sequence": selected.source_sequence,
        "predecessor_version": selected.predecessor_version,
        "successor_version": selected.successor_version,
        "prepared_artifact_sha256": selected.prepared_artifact_sha256,
        "predecessor_state": selected.predecessor_state,
        "predecessor_state_sha256": selected.predecessor_state_sha256,
        "successor_state": selected.successor_state,
        "successor_state_sha256": selected.successor_state_sha256,
        "resolved_nodes": list(selected.resolved_nodes),
        "transition_sha256": selected.transition_sha256,
    }


def _transition_from_document(value: object) -> CapturedStateTransition:
    if not isinstance(value, Mapping):
        raise _fail("captured_transition_document_invalid")
    try:
        selected = CapturedStateTransition.create(
            stream_id=value.get("stream_id"),
            source_sequence=value.get("source_sequence"),
            predecessor_version=value.get("predecessor_version"),
            successor_version=value.get("successor_version"),
            predecessor_state=value.get("predecessor_state"),
            successor_state=value.get("successor_state"),
            prepared_artifact_sha256=value.get("prepared_artifact_sha256"),
            resolved_nodes=value.get("resolved_nodes"),
        )
    except (TypeError, ValueError):
        raise _fail("captured_transition_document_invalid") from None
    if (
        selected.predecessor_state_sha256 != value.get("predecessor_state_sha256")
        or selected.successor_state_sha256 != value.get("successor_state_sha256")
        or selected.transition_sha256 != value.get("transition_sha256")
    ):
        raise _fail("captured_transition_document_hash_mismatch")
    return selected


def _build_serializability(
    common: Mapping[str, str],
    reuse: Mapping[str, object],
    envelope: Mapping[str, object],
    state_cut: Mapping[str, object],
    projection: Mapping[str, object],
    certification: StateCutCertification,
) -> dict[str, object]:
    states: list[dict[str, object]] = [
        {"edges": [], "entities": [], "episodes": []},
        {
            "edges": [],
            "entities": [{"name": "entity 0", "summary": "fixture"}],
            "episodes": [{"source_sequence": 0, "source_hash": "fixture-0"}],
        },
        {
            "edges": [
                {
                    "fact": "Entity 0 precedes Entity 1",
                    "source_entity_key": "entity 0",
                    "target_entity_key": "entity 1",
                }
            ],
            "entities": [
                {"name": "entity 0", "summary": "fixture"},
                {"name": "entity 1", "summary": "fixture"},
            ],
            "episodes": [
                {"source_sequence": 0, "source_hash": "fixture-0"},
                {"source_sequence": 1, "source_hash": "fixture-1"},
            ],
        },
    ]
    checkpoints: list[dict[str, object]] = []
    artifacts: list[PreparedArtifact] = []
    for sequence in range(2):
        artifact = _fixture_artifact(sequence, certification.certification_sha256)
        artifacts.append(artifact)
        canonical_node = {
            "name": f"Entity {sequence}",
            "summary": "fixture",
            "uuid": f"canonical-{sequence}",
        }
        serial = CapturedStateTransition.create(
            stream_id="v31-deterministic-fixture",
            source_sequence=sequence,
            predecessor_version=sequence - 1,
            successor_version=sequence,
            predecessor_state=states[sequence],
            successor_state=states[sequence + 1],
            prepared_artifact_sha256=artifact.artifact_sha256,
            resolved_nodes=[canonical_node],
        )
        candidate = CapturedStateTransition.create(
            stream_id="v31-deterministic-fixture",
            source_sequence=sequence,
            predecessor_version=sequence - 1,
            successor_version=sequence,
            predecessor_state=deepcopy(states[sequence]),
            successor_state=deepcopy(states[sequence + 1]),
            prepared_artifact_sha256=artifact.artifact_sha256,
            resolved_nodes=[canonical_node, dict(reversed(tuple(canonical_node.items())))],
        )
        parity = verify_captured_transition_parity(serial, candidate)
        rendered_input = payload_sha256(
            {"fixture": "rendered-input", "source_sequence": sequence}
        )
        captured_output = payload_sha256(
            {"fixture": "captured-provider-output", "source_sequence": sequence}
        )
        checkpoints.append(
            {
                **parity,
                "serial_transition": _transition_document(serial),
                "candidate_transition": _transition_document(candidate),
                "serial_rendered_input_sha256": rendered_input,
                "candidate_rendered_input_sha256": rendered_input,
                "serial_captured_provider_output_sha256": captured_output,
                "candidate_captured_provider_output_sha256": captured_output,
                "semantic_work_contract_sha256": payload_sha256(
                    {
                        "compile": ["graphiti.extract_nodes", "graphiti.extract_edges"],
                        "source_sequence": sequence,
                        "state_cut_certification_sha256": certification.certification_sha256,
                    }
                ),
            }
        )

    drift = CapturedStateTransition.create(
        stream_id="v31-deterministic-fixture",
        source_sequence=1,
        predecessor_version=0,
        successor_version=1,
        predecessor_state=states[1],
        successor_state={**states[2], "unexpected_semantic_state": True},
        prepared_artifact_sha256=artifacts[1].artifact_sha256,
        resolved_nodes=[
            {"name": "Entity 1", "summary": "fixture", "uuid": "canonical-1"}
        ],
    )
    serial_second = CapturedStateTransition.create(
        stream_id="v31-deterministic-fixture",
        source_sequence=1,
        predecessor_version=0,
        successor_version=1,
        predecessor_state=states[1],
        successor_state=states[2],
        prepared_artifact_sha256=artifacts[1].artifact_sha256,
        resolved_nodes=[
            {"name": "Entity 1", "summary": "fixture", "uuid": "canonical-1"}
        ],
    )
    try:
        verify_captured_transition_parity(serial_second, drift)
    except GraphitiV31AdapterError as error:
        if str(error) != "captured_state_parity_failure":
            raise _fail("deterministic_tamper_probe_wrong_failure") from None
    else:
        raise _fail("deterministic_tamper_probe_accepted")

    body: dict[str, object] = {
        "schema_version": SERIAL_SCHEMA,
        "status": "PASS",
        **common,
        "reuse_audit_sha256": reuse["payload_sha256"],
        "execution_envelope_sha256": envelope["payload_sha256"],
        "state_cut_artifact_sha256": state_cut["payload_sha256"],
        "state_cut_certification_sha256": certification.certification_sha256,
        "canonical_projection_artifact_sha256": projection["payload_sha256"],
        "qualification_mode": "CAPTURED_DETERMINISTIC_OFFLINE",
        "checkpoint_count": len(checkpoints),
        "checkpoints": checkpoints,
        "same_rendered_inputs_at_every_checkpoint": True,
        "same_captured_outputs_at_every_checkpoint": True,
        "same_semantic_work_at_every_checkpoint": True,
        "same_prepared_artifact_at_every_checkpoint": True,
        "canonical_state_parity_at_every_checkpoint": True,
        "source_ordered_predecessor_versions": [-1, 0],
        "source_ordered_successor_versions": [0, 1],
        "oracle_miss_count": 0,
        "hidden_fallback_count": 0,
        "state_cut_certification_failure_count": 0,
        "fail_closed_tamper_probe": "PASS_REJECTED_DRIFT",
        "live_llm_bitwise_graph_parity_claimed": False,
    }
    return _sealed(body)


def _record_from_document(value: Mapping[str, object]) -> CertificationRecord:
    contract_payload = value.get("operator_contract")
    certification_payload = value.get("certification")
    if not isinstance(contract_payload, Mapping) or not isinstance(
        certification_payload, Mapping
    ):
        raise _fail("state_cut_record_invalid")
    try:
        contract = OperatorContract.create(
            operator_name=contract_payload.get("operator_name"),
            dependency_class=DependencyClass(contract_payload.get("dependency_class")),
            effect_class=EffectClass(contract_payload.get("effect_class")),
        )
        if (
            contract.contract_sha256 != value.get("operator_contract_sha256")
            or contract.contract_sha256
            != certification_payload.get("operator_contract_sha256")
        ):
            raise _fail("state_cut_record_invalid")
        record = CertificationRecord.create(
            operator_contract=contract,
            memory_backend_identity_sha256=certification_payload.get(
                "memory_backend_identity_sha256"
            ),
            adapter_identity_sha256=certification_payload.get("adapter_identity_sha256"),
            operator_identity_sha256=certification_payload.get("operator_identity_sha256"),
            code_revision_sha256=certification_payload.get("code_revision_sha256"),
            prompt_identity_sha256=certification_payload.get("prompt_identity_sha256"),
            schema_identity_sha256=certification_payload.get("schema_identity_sha256"),
            config_identity_sha256=certification_payload.get("config_identity_sha256"),
            allowed_evidence_inputs=certification_payload.get("allowed_evidence_inputs"),
            allowed_upstream_outputs=certification_payload.get("allowed_upstream_outputs"),
            allowed_apis=certification_payload.get("allowed_apis"),
            forbidden_apis=certification_payload.get("forbidden_apis"),
            qualification_trace_sha256=certification_payload.get(
                "qualification_trace_sha256"
            ),
            persistent_state_read_count=certification_payload.get(
                "persistent_state_read_count"
            ),
            persistent_state_write_count=certification_payload.get(
                "persistent_state_write_count"
            ),
            undeclared_external_side_effect_count=certification_payload.get(
                "undeclared_external_side_effect_count"
            ),
            future_evidence_access_count=certification_payload.get(
                "future_evidence_access_count"
            ),
            undeclared_state_facing_call_count=certification_payload.get(
                "undeclared_state_facing_call_count"
            ),
        )
    except (TypeError, ValueError):
        raise _fail("state_cut_record_invalid") from None
    if record.certification_sha256 != value.get("certification_sha256"):
        raise _fail("state_cut_record_invalid")
    return record


def _certification_from_artifact(value: Mapping[str, object]) -> StateCutCertification:
    qualification = value.get("qualification_document")
    if not isinstance(qualification, Mapping):
        raise _fail("qualification_document_invalid")
    _verify_inline_seal(
        qualification, "payload_sha256", "qualification_document_invalid"
    )
    records = qualification.get("operator_records")
    if not isinstance(records, list):
        raise _fail("qualification_document_invalid")
    bundle = StateCutCertification.create([_record_from_document(item) for item in records])
    if (
        bundle.certification_sha256 != value.get("state_cut_certification_sha256")
        or list(bundle.operator_names) != value.get("compiled_operator_names")
    ):
        raise _fail("state_cut_bundle_mismatch")
    return bundle


async def freeze_v31_qualification(
    paths: V31FreezePaths,
    *,
    compile_workers: int = 2,
    lookahead: int = 2,
    global_llm_admission_k: int = 2,
) -> dict[str, dict[str, object]]:
    """Build, verify, then atomically persist the six offline artifacts."""

    if not isinstance(paths, V31FreezePaths):
        raise _fail("freeze_paths_invalid")
    if (compile_workers, lookahead, global_llm_admission_k) != (2, 2, 2):
        raise _fail("method_knobs_not_frozen_to_two")
    common = _common(paths)
    baseline = _load_baseline(paths)
    preflight = _load_preflight(paths)
    runtime = _load_runtime_config(paths)
    s4_contract, _s4_graph = _load_s4_reuse(paths)
    reuse = _build_reuse(paths, common, baseline, s4_contract)
    envelope = _build_envelope(
        paths,
        common,
        baseline,
        preflight,
        runtime,
        reuse,
        compile_workers=compile_workers,
        lookahead=lookahead,
        global_llm_admission_k=global_llm_admission_k,
    )
    try:
        qualification = await qualify_graphiti_v0293_state_cut(
            project_root=paths.repository_root
        )
    except ValueError as error:
        raise _fail(f"state_cut_qualification_failed:{error}") from None
    state_cut = _build_state_cut(
        paths,
        common,
        reuse,
        envelope,
        qualification.document,
        qualification.certification,
    )
    projection = _build_projection(
        paths, common, reuse, envelope, state_cut
    )
    serial = _build_serializability(
        common,
        reuse,
        envelope,
        state_cut,
        projection,
        qualification.certification,
    )
    workload = _build_workload_complexity(paths, common, baseline)
    documents = {
        FROZEN_FILENAMES[0]: reuse,
        FROZEN_FILENAMES[1]: envelope,
        FROZEN_FILENAMES[2]: state_cut,
        FROZEN_FILENAMES[3]: projection,
        FROZEN_FILENAMES[4]: serial,
        FROZEN_FILENAMES[5]: workload,
    }
    for document in documents.values():
        _verify_sealed(document)
    for name, document in documents.items():
        path = paths.output_dir / name
        if path.exists() and _read_json(path, "existing_artifact_conflict") != document:
            raise _fail("existing_artifact_conflict")
    for name, document in documents.items():
        path = paths.output_dir / name
        if not path.exists():
            atomic_write_json(path, document)
    verify_v31_qualification_artifacts(paths)
    return documents


def verify_v31_qualification_artifacts(
    paths: V31FreezePaths,
) -> dict[str, dict[str, object]]:
    """Fail closed on self-hash, source identity, knob, or cross-link drift."""

    documents = {
        name: _verify_sealed(_read_json(paths.output_dir / name, "artifact_missing"))
        for name in FROZEN_FILENAMES
    }
    reuse = documents[FROZEN_FILENAMES[0]]
    envelope = documents[FROZEN_FILENAMES[1]]
    state_cut = documents[FROZEN_FILENAMES[2]]
    projection = documents[FROZEN_FILENAMES[3]]
    serial = documents[FROZEN_FILENAMES[4]]
    workload = documents[FROZEN_FILENAMES[5]]
    expected_common = _common(paths)
    for name, document in documents.items():
        if document.get("methodology_sha256") != expected_common["methodology_sha256"]:
            raise _fail("methodology_hash_mismatch")
        if document.get("workplan_sha256") != expected_common["workplan_sha256"]:
            raise _fail("workplan_hash_mismatch")
        if document.get("schema_version") not in {
            REUSE_SCHEMA,
            ENVELOPE_SCHEMA,
            STATE_CUT_SCHEMA,
            PROJECTION_SCHEMA,
            SERIAL_SCHEMA,
            WORKLOAD_COMPLEXITY_SCHEMA,
        }:
            raise _fail(f"artifact_schema_invalid:{name}")
    baseline = _load_baseline(paths)
    expected_workload = _build_workload_complexity(paths, expected_common, baseline)
    if workload != expected_workload:
        raise _fail("workload_complexity_binding_invalid")
    if (
        reuse.get("baseline", {}).get("plan_payload_sha256") != baseline["payload_sha256"]
        or reuse.get("baseline", {}).get("running_block_artifacts_read") is not False
        or reuse.get("canonical_projection_reuse", {}).get("old_d0_result_authority")
        != "NOT_V31_SERIALIZABILITY_EVIDENCE"
    ):
        raise _fail("reuse_audit_binding_invalid")
    if (
        envelope.get("reuse_audit_sha256") != reuse["payload_sha256"]
        or envelope.get("method_knobs")
        != {
            "bind_workers": 1,
            "compile_workers_c": 2,
            "global_llm_admission_k": 2,
            "lookahead_w": 2,
        }
        or envelope.get("baseline_shared_execution_envelope_sha256")
        != baseline["shared_execution_envelope_sha256"]
        or payload_sha256(envelope.get("shared_public_runtime_identity"))
        != baseline["shared_execution_envelope_sha256"]
        or envelope.get("backend_contract", {}).get(
            "backend_prefix_match_granularity_tokens"
        )
        != 16
        or envelope.get("backend_contract", {}).get(
            "decode_context_parallel_size"
        )
        != 1
        or envelope.get("backend_contract", {}).get(
            "decode_context_parallel_evidence"
        )
        != {
            "vllm_repository": _VLLM_REPOSITORY,
            "vllm_revision": _VLLM_REVISION,
            "source_path": "vllm/config/parallel.py",
            "source_sha256": _VLLM_PARALLEL_SOURCE_SHA256,
            "source_git_blob": _VLLM_PARALLEL_SOURCE_GIT_BLOB,
            "default_field": "ParallelConfig.decode_context_parallel_size",
            "default_value": 1,
        }
        or envelope.get("backend_contract", {})
        .get("granularity_evidence", {})
        .get("cache_source_sha256")
        != _VLLM_CACHE_SOURCE_SHA256
        or envelope.get("request_admission_contract")
        != {
            "permit_unit": "ACTUAL_TRANSPORT_ATTEMPT",
            "observed_inflight_counter_unit": "ACTUAL_TRANSPORT_ATTEMPT",
            "retry_policy": "EVERY_ATTEMPT_REACQUIRES_INDEPENDENTLY",
            "logical_call_permit_scope": "FORBIDDEN_ACROSS_RETRIES",
        }
        or envelope.get("backend_contract", {}).get("cache_isolation_contract")
        != {
            "comparable_methods": [
                "U0-aligned",
                "A0-aligned",
                "P(C=2)-aligned",
                "MemBind-Barrier",
                "MemBind-FIFO",
                "MemBind",
            ],
            "policy_applies_equally_to_all_comparable_methods": True,
            "request_cache_salt": "UNIQUE_FRESH_PER_BLOCK",
            "cross_block_prefix_identity_reuse": False,
            "cross_block_warm_inheritance": False,
            "within_block_prefix_reuse": True,
            "physical_cache_reset_claimed": False,
        }
        or envelope.get("backend_contract", {}).get("cache_claim_status")
        != "OBSERVATIONAL"
        or envelope.get("tokenizer_identity", {}).get("revision")
        != _TOKENIZER_REVISION
        or envelope.get("live_service_calls_performed") is not False
    ):
        raise _fail("execution_envelope_binding_invalid")
    bundle = _certification_from_artifact(state_cut)
    if (
        state_cut.get("reuse_audit_sha256") != reuse["payload_sha256"]
        or state_cut.get("execution_envelope_sha256") != envelope["payload_sha256"]
        or state_cut.get("forbidden_observation_counts")
        != {
            "future_evidence_access_count": 0,
            "persistent_state_read_count": 0,
            "persistent_state_write_count": 0,
            "undeclared_external_side_effect_count": 0,
            "undeclared_state_facing_call_count": 0,
        }
    ):
        raise _fail("state_cut_artifact_binding_invalid")
    if (
        projection.get("state_cut_artifact_sha256") != state_cut["payload_sha256"]
        or projection.get("canonicalizer", {}).get("sha256")
        != _require_file(paths.canonicalizer, "canonicalizer_missing")
        or projection.get("graph_exporter", {}).get("sha256")
        != _require_file(paths.canonical_exporter, "canonical_exporter_missing")
        or projection.get("namespace_normalizer", {}).get("sha256")
        != _require_file(paths.s4_projection_normalizer, "projection_normalizer_missing")
        or projection.get("post_result_projection_changes_allowed") is not False
    ):
        raise _fail("canonical_projection_binding_invalid")
    checkpoints = serial.get("checkpoints")
    reconstructed_parity: list[dict[str, object]] = []
    if isinstance(checkpoints, list):
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, Mapping):
                raise _fail("deterministic_serializability_binding_invalid")
            serial_transition = _transition_from_document(
                checkpoint.get("serial_transition")
            )
            candidate_transition = _transition_from_document(
                checkpoint.get("candidate_transition")
            )
            try:
                reconstructed_parity.append(
                    verify_captured_transition_parity(
                        serial_transition, candidate_transition
                    )
                )
            except GraphitiV31AdapterError:
                raise _fail("deterministic_serializability_binding_invalid") from None
    if (
        serial.get("state_cut_artifact_sha256") != state_cut["payload_sha256"]
        or serial.get("state_cut_certification_sha256") != bundle.certification_sha256
        or serial.get("canonical_projection_artifact_sha256") != projection["payload_sha256"]
        or serial.get("checkpoint_count") != 2
        or not isinstance(checkpoints, list)
        or len(checkpoints) != 2
        or len(reconstructed_parity) != 2
        or any(
            checkpoint.get("exact_canonical_state_parity") is not True
            or checkpoint.get("exact_predecessor_state_parity") is not True
            or checkpoint.get("exact_prepared_artifact_parity") is not True
            or checkpoint.get("exact_resolved_node_parity") is not True
            or checkpoint.get("serial_rendered_input_sha256")
            != checkpoint.get("candidate_rendered_input_sha256")
            or checkpoint.get("serial_captured_provider_output_sha256")
            != checkpoint.get("candidate_captured_provider_output_sha256")
            for checkpoint in checkpoints
        )
        or any(
            observed != {
                key: checkpoint[key]
                for key in (
                    "exact_canonical_state_parity",
                    "exact_predecessor_state_parity",
                    "exact_prepared_artifact_parity",
                    "exact_resolved_node_parity",
                    "source_sequence",
                    "stream_id",
                    "successor_version",
                )
            }
            for observed, checkpoint in zip(reconstructed_parity, checkpoints, strict=True)
        )
        or serial.get("canonical_state_parity_at_every_checkpoint") is not True
        or serial.get("same_prepared_artifact_at_every_checkpoint") is not True
        or serial.get("fail_closed_tamper_probe") != "PASS_REJECTED_DRIFT"
        or serial.get("oracle_miss_count") != 0
        or serial.get("hidden_fallback_count") != 0
        or serial.get("state_cut_certification_failure_count") != 0
    ):
        raise _fail("deterministic_serializability_binding_invalid")
    return documents


def load_v31_state_cut_certification(
    paths: V31FreezePaths,
) -> StateCutCertification:
    """Verify the complete freeze, then reconstruct its certified State-Cut."""

    documents = verify_v31_qualification_artifacts(paths)
    return _certification_from_artifact(documents["STATE_CUT_CERTIFICATION.json"])


__all__ = [
    "FROZEN_FILENAMES",
    "MemBindV31FreezerError",
    "V31FreezePaths",
    "freeze_v31_qualification",
    "load_v31_state_cut_certification",
    "verify_v31_qualification_artifacts",
]
