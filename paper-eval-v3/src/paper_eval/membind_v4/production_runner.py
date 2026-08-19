"""Production callback for the bounded v4 c01 development prefixes.

The callback is deliberately separate from :mod:`runner`: ``runner`` owns the
append-only candidate ledger, while this module owns the sealed v3.1 source
inventory and live block composition.  Constructing the callback is offline;
environment, episode, and State-Cut inputs are loaded only when a READY live
candidate invokes it.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.apc_aligned_baseline import (
    APC_BASELINE_HISTORIES,
    build_apc_aligned_baseline_plan,
)
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.live_block import V31LiveHooks
from paper_eval.membind_v31.method_plan import (
    build_membind_v31_live_plan,
    verify_membind_v31_method_plan,
)
from paper_eval.membind_v31.production_executor import ProductionExecutorPaths
from paper_eval.membind_v4.autoresearch import (
    CandidateStore,
    assess_candidate,
    candidate_config,
)
from paper_eval.membind_v4.live_block import (
    V4ProductionLoaders,
    execute_v4_live_block,
    production_v4_loaders,
)


CANDIDATE_HISTORY_ID = "07741c45"
CANDIDATE_SOURCE_COUNTS = (6, 12)


class V4ProductionRunnerError(ValueError):
    """A live candidate source, plan, or result drifted from preregistration."""


def _fail(code: str) -> V4ProductionRunnerError:
    return V4ProductionRunnerError(code)


def _read_prior_sealed(path: Path, label: str, schema: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _fail(f"prior_six_{label}_unreadable") from error
    if not isinstance(value, dict):
        raise _fail(f"prior_six_{label}_invalid")
    body = dict(value)
    digest = body.pop("payload_sha256", None)
    if not isinstance(digest, str) or digest != payload_sha256(body):
        raise _fail(f"prior_six_{label}_payload_hash_mismatch")
    if body.get("schema_version") != schema:
        raise _fail(f"prior_six_{label}_schema_invalid")
    body["payload_sha256"] = digest
    return body


def verify_prior_six_reduction(
    reduction_path: Path,
    *,
    candidate_id: str,
    history_id: str,
) -> dict[str, object]:
    """Verify the sealed six-source admission proof for a 12-source run."""

    path = Path(reduction_path)
    root = path.parent
    reduction = _read_prior_sealed(
        path,
        "reduction",
        "membind.paper-eval-v4.candidate-reduction.v1",
    )
    candidate = _read_prior_sealed(
        root / "candidate.json",
        "candidate",
        "membind.paper-eval-v4.candidate.v1",
    )
    summary = _read_prior_sealed(
        root / "summary.json",
        "summary",
        "membind.paper-eval-v4.summary.v1",
    )
    if (
        candidate.get("candidate_id") != candidate_id
        or summary.get("candidate_id") != candidate_id
        or reduction.get("candidate_id") != candidate_id
        or candidate.get("source_count") != 6
        or summary.get("source_count") != 6
        or reduction.get("source_count") != 6
    ):
        raise _fail("prior_six_candidate_identity_drift")
    if candidate.get("status") != "COMPLETED":
        raise _fail("prior_six_candidate_not_completed")
    config = candidate_config(candidate_id)
    if any(
        candidate.get(field) != config.get(field)
        for field in ("policy", "global_k", "speculation_distance", "phase_complementary")
    ):
        raise _fail("prior_six_policy_drift")
    if summary.get("history_id") != history_id:
        raise _fail("prior_six_history_drift")
    if (
        summary.get("status") != "PASS"
        or summary.get("runner_mode") != "live"
        or reduction.get("status") != "PASS"
    ):
        raise _fail("prior_six_status_invalid")
    decision = reduction.get("decision")
    if not isinstance(decision, Mapping) or decision.get("decision") != "EXTEND_TO_12":
        raise _fail("prior_six_decision_invalid")
    mechanism = reduction.get("mechanism")
    performance = reduction.get("performance")
    if not isinstance(mechanism, Mapping) or not isinstance(performance, Mapping):
        raise _fail("prior_six_evidence_invalid")
    mechanism_fields = (
        "qualified_node_resolve_count",
        "speculation_launch_count",
        "exact_validation_completed_count",
        "semantic_hit_count",
        "semantic_miss_count",
        "overlap_count",
        "hidden_critical_time_ns",
        "direct_violation_count",
    )
    if any(mechanism.get(field, 0) != summary.get(field, 0) for field in mechanism_fields):
        raise _fail("prior_six_mechanism_evidence_drift")
    if "freshness_p95_ratio" not in performance or "makespan_ratio" not in performance:
        raise _fail("prior_six_performance_evidence_invalid")
    recomputed = assess_candidate(
        {
            **dict(summary),
            **{field: mechanism.get(field, 0) for field in mechanism_fields},
            "freshness_p95_ratio": performance.get("freshness_p95_ratio"),
            "makespan_ratio": performance.get("makespan_ratio"),
        }
    )
    if recomputed != dict(decision):
        raise _fail("prior_six_decision_drift")
    return {
        "candidate_id": candidate_id,
        "history_id": history_id,
        "source_count": 6,
        "decision": "EXTEND_TO_12",
        "absolute_path": str(path.resolve()),
        "file_sha256": sha256_file(path),
        "payload_sha256": reduction["payload_sha256"],
        "candidate_payload_sha256": candidate["payload_sha256"],
        "summary_payload_sha256": summary["payload_sha256"],
    }


def build_v4_candidate_plan(
    canonical_plan: Mapping[str, object],
    *,
    candidate_id: str,
    source_count: int,
    candidate_root: Path,
) -> dict[str, object]:
    """Derive a fresh verified v3.1 plan for c01 sources 0..5 or 0..11."""

    if candidate_id != "c01":
        raise _fail("candidate_policy_not_implemented")
    if (
        isinstance(source_count, bool)
        or not isinstance(source_count, int)
        or source_count not in CANDIDATE_SOURCE_COUNTS
    ):
        raise _fail("candidate_source_count_invalid")
    try:
        canonical = verify_membind_v31_method_plan(canonical_plan)
    except ValueError:
        raise _fail("canonical_plan_invalid") from None
    root = Path(candidate_root).resolve()
    try:
        inventory = {
            history: list(canonical["history_source_sha256s"][history])
            for history in APC_BASELINE_HISTORIES
        }
        inventory[CANDIDATE_HISTORY_ID] = inventory[CANDIDATE_HISTORY_ID][
            :source_count
        ]
        baseline = build_apc_aligned_baseline_plan(
            run_id=canonical["baseline_run_id"],
            history_source_sha256s=inventory,
            interarrival_ns=canonical["interarrival_ns"],
            execution_envelope_sha256=canonical[
                "shared_execution_envelope_sha256"
            ],
            service_reference_ns=canonical["service_reference_ns"],
            normalized_offered_load=canonical["normalized_offered_load"],
        )
    except (KeyError, TypeError, ValueError):
        raise _fail("candidate_baseline_projection_invalid") from None
    digest = hashlib.sha256(
        (
            f"{canonical['payload_sha256']}\0{candidate_id}\0{source_count}\0{root}"
        ).encode("utf-8")
    ).hexdigest()
    try:
        plan = build_membind_v31_live_plan(
            run_id=f"membind-v31-v4-ar-{digest[:24]}",
            verified_baseline_plan=baseline,
            methodology_sha256=canonical["methodology_sha256"],
            workplan_sha256=canonical["workplan_sha256"],
        )
        verified = verify_membind_v31_method_plan(plan)
    except (KeyError, TypeError, ValueError):
        raise _fail("candidate_plan_derivation_invalid") from None
    expected_offsets = canonical["arrival_traces"][CANDIDATE_HISTORY_ID][
        "arrival_offsets_ns"
    ][:source_count]
    block = verified["blocks"][0]
    if (
        block.get("method") != "MemBind"
        or block.get("history_id") != CANDIDATE_HISTORY_ID
        or block.get("source_count") != source_count
        or verified["arrival_traces"][CANDIDATE_HISTORY_ID][
            "arrival_offsets_ns"
        ]
        != expected_offsets
        or verified["compile_workers"] != canonical["compile_workers"]
        or verified["lookahead"] != canonical["lookahead"]
        or verified["global_llm_admission_k"]
        != canonical["global_llm_admission_k"]
        or block["namespace"] == canonical["blocks"][0]["namespace"]
    ):
        raise _fail("candidate_plan_identity_drift")
    return verified


def build_v4_candidate_live_runner(
    *,
    paths: ProductionExecutorPaths | None = None,
    loaders: V4ProductionLoaders | None = None,
    base_hooks_factory: Callable[[], V31LiveHooks] | None = None,
    factorized_adapter_factory: Callable[[object, StateCutCertification], object]
    | None = None,
    execute_block: Callable[..., object] = execute_v4_live_block,
    prior_six_reduction_path: Path | None = None,
) -> Callable[..., Mapping[str, object]]:
    """Build the live callback accepted by :func:`run_candidate`.

    The formal source inventory is verified when this factory is built.  Live
    inputs remain lazy so a failed service preflight cannot initialize Graphiti
    or create a namespace.
    """

    selected_paths = (
        ProductionExecutorPaths.from_repository(Path(__file__).resolve().parents[4])
        if paths is None
        else paths
    )
    if not isinstance(selected_paths, ProductionExecutorPaths):
        raise _fail("production_paths_invalid")
    selected_loaders = (
        production_v4_loaders(selected_paths) if loaders is None else loaders
    )
    if not isinstance(selected_loaders, V4ProductionLoaders):
        raise _fail("production_loaders_invalid")
    if not callable(execute_block):
        raise _fail("execute_block_invalid")
    try:
        canonical = verify_membind_v31_method_plan(
            selected_loaders.load_plan(selected_paths.control_root)
        )
    except ValueError:
        raise _fail("canonical_plan_invalid") from None
    loaded: dict[str, object] = {}

    def context() -> tuple[
        Mapping[str, str],
        StateCutCertification,
        Mapping[str, Sequence[object]],
    ]:
        if not loaded:
            env = selected_loaders.load_env(selected_paths.env_file)
            certification = selected_loaders.load_certification(
                selected_paths.freeze_paths
            )
            episodes = selected_loaders.load_episodes(
                selected_paths.development_input, canonical
            )
            if (
                not isinstance(env, Mapping)
                or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in env.items()
                )
                or not isinstance(certification, StateCutCertification)
                or tuple(episodes) != tuple(canonical["histories"])
            ):
                raise _fail("production_context_invalid")
            loaded.update(
                env=dict(env), certification=certification, episodes=episodes
            )
        return (  # type: ignore[return-value]
            loaded["env"],
            loaded["certification"],
            loaded["episodes"],
        )

    def run_candidate_prefix(**kwargs: object) -> Mapping[str, object]:
        store = kwargs.get("store")
        history_id = kwargs.get("history_id")
        source_count = kwargs.get("source_count")
        if not isinstance(store, CandidateStore):
            raise _fail("candidate_store_invalid")
        if history_id != CANDIDATE_HISTORY_ID:
            raise _fail("candidate_history_invalid")
        if source_count != store.source_count:
            raise _fail("candidate_source_identity_drift")
        prior_six_binding: dict[str, object] | None = None
        if source_count == 12:
            if prior_six_reduction_path is None:
                raise _fail("prior_six_reduction_required")
            prior_six_binding = verify_prior_six_reduction(
                prior_six_reduction_path,
                candidate_id=store.candidate_id,
                history_id=history_id,
            )
        elif prior_six_reduction_path is not None:
            raise _fail("prior_six_reduction_unexpected")
        plan = build_v4_candidate_plan(
            canonical,
            candidate_id=store.candidate_id,
            source_count=source_count,  # type: ignore[arg-type]
            candidate_root=store.root,
        )
        block_indices = [
            index
            for index, block in enumerate(plan["blocks"])
            if block["method"] == "MemBind"
            and block["history_id"] == CANDIDATE_HISTORY_ID
        ]
        if len(block_indices) != 1:
            raise _fail("candidate_block_invalid")
        block_index = block_indices[0]
        block = plan["blocks"][block_index]
        env, certification, episodes = context()
        selected_episodes = episodes[CANDIDATE_HISTORY_ID][:source_count]
        if len(selected_episodes) != source_count:
            raise _fail("candidate_episode_prefix_invalid")
        hooks = base_hooks_factory() if base_hooks_factory is not None else None
        produced = execute_block(
            verified_plan=plan,
            block_index=block_index,
            episodes=selected_episodes,
            env=env,
            block_root=store.root / "block",
            state_cut_certification=certification,
            compile_workers=int(plan["compile_workers"]),
            lookahead=int(plan["lookahead"]),
            stream_id=CANDIDATE_HISTORY_ID,
            namespace_override=None,
            base_hooks=hooks,
            factorized_adapter_factory=factorized_adapter_factory,
        )
        result = asyncio.run(produced) if inspect.isawaitable(produced) else produced
        if not isinstance(result, Mapping):
            raise _fail("candidate_block_result_invalid")
        performance = result.get("performance")
        telemetry = result.get("telemetry")
        freshness = (
            performance.get("freshness_ns")
            if isinstance(performance, Mapping)
            else None
        )
        if (
            result.get("status") != "PASS"
            or result.get("run_id") != plan["run_id"]
            or result.get("history_id") != CANDIDATE_HISTORY_ID
            or result.get("namespace") != block["namespace"]
            or result.get("source_count") != source_count
            or result.get("direct_violation_count") != 0
            or not isinstance(performance, Mapping)
            or isinstance(freshness, (str, bytes))
            or not isinstance(freshness, Sequence)
            or len(freshness) != source_count
            or not isinstance(telemetry, Mapping)
            or telemetry.get("persistent_write_count") != 0
        ):
            raise _fail("candidate_block_result_invalid")
        return {
            "schema_version": "membind.paper-eval-v4.candidate-live-result.v1",
            "status": "PASS",
            "stream_id": CANDIDATE_HISTORY_ID,
            "source_count": source_count,
            "publication_source_sequences": list(range(source_count)),
            "direct_violation_count": 0,
            "performance": deepcopy(dict(performance)),
            "telemetry": deepcopy(dict(telemetry)),
            "prior_six_binding": deepcopy(prior_six_binding),
            "admission_observation": deepcopy(
                result.get("admission_observation")
            ),
            "output_artifacts": {
                "block_root": str((store.root / "block").resolve()),
                "candidate_plan_payload_sha256": plan["payload_sha256"],
                "candidate_plan_run_id": plan["run_id"],
                "v4_block_result_payload_sha256": result.get("payload_sha256"),
            },
        }

    return run_candidate_prefix


__all__ = [
    "CANDIDATE_HISTORY_ID",
    "CANDIDATE_SOURCE_COUNTS",
    "V4ProductionRunnerError",
    "build_v4_candidate_live_runner",
    "build_v4_candidate_plan",
    "verify_prior_six_reduction",
]
