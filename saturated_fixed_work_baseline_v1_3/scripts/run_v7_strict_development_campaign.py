#!/usr/bin/env python3
"""Run the strict-schema Bailian/SiliconFlow V7 2+6+6 development campaign."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import run_v7_composite_development_campaign as base  # noqa: E402

from saturated_fixed_work_baseline_v1_3.membind_v7.characterization import (  # noqa: E402
    audit_r1_assumptions,
    build_r2_causal_trace,
    characterize_r3_blocks,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.continuation import (  # noqa: E402
    ContinuationStatus,
    audit_continuation_source,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.development_campaign import (  # noqa: E402
    build_development_failure,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.development_provider_diagnostics import (  # noqa: E402
    augment_development_failure,
    install_development_schema_diagnostics,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.engineering_observer_runtime import (  # noqa: E402
    build_embedding_preflight_evidence,
    summarize_provider_observations,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.graphiti_observer import (  # noqa: E402
    current_provider_observation_scope,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import (  # noqa: E402
    ObserverAttemptJournal,
    run_graphiti_observer_block_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.pins import (  # noqa: E402
    GRAPHITI_VERSION,
    verify_membind_pin,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.provider_independent_development_campaign import (  # noqa: E402
    materialize_provider_development_artifacts,
    record_provider_development_success,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.strict_development_campaign import (  # noqa: E402
    load_strict_development_protocol,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.strict_development_runtime import (  # noqa: E402
    STRICT_PROVIDER_IDENTITY_KIND,
    build_strict_development_runtime,
)


PROTOCOL = base.PROJECT / "v7/BAILIAN_QWEN3_MAX_STRICT_V7_DEVELOPMENT_PROTOCOL_V2.json"
STRICT_RUNTIME_FREEZE = (
    base.PROJECT / "v7/BAILIAN_QWEN3_MAX_STRICT_DEVELOPMENT_RUNTIME_FREEZE.json"
)
RUN_ID = base.re.compile(r"v7-development-strict-[a-z0-9][a-z0-9-]{2,79}")


def _source_bindings() -> dict[str, str]:
    result = base._source_bindings()
    additions = {
        "saturated_fixed_work_baseline_v1_3/scripts/run_v7_strict_development_campaign.py": (
            Path(__file__).resolve()
        ),
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/development_provider_diagnostics.py": (
            base.PROJECT
            / "src/saturated_fixed_work_baseline_v1_3/membind_v7/development_provider_diagnostics.py"
        ),
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/provider_independent_development_campaign.py": (
            base.PROJECT
            / "src/saturated_fixed_work_baseline_v1_3/membind_v7/provider_independent_development_campaign.py"
        ),
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/strict_development_campaign.py": (
            base.PROJECT
            / "src/saturated_fixed_work_baseline_v1_3/membind_v7/strict_development_campaign.py"
        ),
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/strict_development_runtime.py": (
            base.PROJECT
            / "src/saturated_fixed_work_baseline_v1_3/membind_v7/strict_development_runtime.py"
        ),
    }
    result.update({name: base._sha256(path) for name, path in additions.items()})
    return dict(sorted(result.items()))


async def _run_block(
    *,
    run_id: str,
    block_id: str,
    spec: Mapping[str, Any],
    authority: Mapping[str, Any],
    environment: Mapping[str, str],
    observations: list[dict[str, Any]],
    embedding_preflights: list[dict[str, Any]],
) -> dict[str, Any]:
    from native_characterization_instrumentation import (
        install_native_characterization_instrumentation,
    )
    from native_characterization_tracing import TraceRecorder

    holder: dict[str, Any] = {}

    async def runtime_builder() -> Any:
        runtime = build_strict_development_runtime(
            env=environment,
            request_id_prefix=f"membind-v7:{run_id}:{block_id.casefold()}",
            strict_freeze_path=STRICT_RUNTIME_FREEZE,
            response_observer=lambda row: observations.append(dict(row)),
        )
        install_development_schema_diagnostics(
            runtime.validated_llm,
            scope_reader=current_provider_observation_scope,
        )
        holder["runtime"] = runtime
        before_ns = time.monotonic_ns()
        vector = await runtime.embedder.create(
            "MemBind V7 development embedding dimension preflight."
        )
        embedding_preflights.append(
            build_embedding_preflight_evidence(
                duration_ns=time.monotonic_ns() - before_ns,
                vector=vector,
            )
        )
        vector = []
        return runtime

    namespace = f"membind-{run_id}-{block_id}".casefold()
    try:
        return await run_graphiti_observer_block_async(
            run_id=run_id,
            block_id=block_id,
            namespace=namespace,
            episodes=base._selected_episodes(authority, spec),
            runtime_builder=runtime_builder,
            recorder_factory=TraceRecorder,
            instrumentation_installer=install_native_characterization_instrumentation,
        )
    finally:
        runtime = holder.get("runtime")
        if runtime is not None:
            await base._close(runtime.construction_transport)
            await base._close(runtime.embedding_transport)


def _preflight() -> dict[str, Any]:
    import graphiti_core

    frozen = load_strict_development_protocol(PROTOCOL)
    source_sha256 = _source_bindings()
    pin = verify_membind_pin(base.ROOT)
    continuation = audit_continuation_source(Path(graphiti_core.__file__).resolve().parent)
    if (
        base._sha256(base.DATASET) != frozen["workload"]["local_file_sha256"]
        or source_sha256 != frozen["observer_harness"]["source_sha256"]
        or pin["native_subject_match"] is not True
        or importlib.metadata.version("graphiti-core") != GRAPHITI_VERSION
        or continuation.status != ContinuationStatus.SUPPORTED_WITH_GUARD
        or base._sha256(base.METHOD_SELECTION)
        != frozen["scientific_method_selection"]["sha256"]
    ):
        raise RuntimeError("V7 strict development campaign preflight failed")
    return {
        "schema_version": "membind.v7.strict-development-preflight.v1",
        "status": "PASS",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "provider_identity_kind": STRICT_PROVIDER_IDENTITY_KIND,
        "protocol_sha256": base._sha256(PROTOCOL),
        "strict_runtime_freeze_sha256": base._sha256(STRICT_RUNTIME_FREEZE),
        "dataset_sha256": base._sha256(base.DATASET),
        "scientific_method_selection_sha256": base._sha256(base.METHOD_SELECTION),
        "source_sha256": source_sha256,
        "provider_calls": 0,
        "embedding_calls": 0,
        "database_calls": 0,
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
    }


def _identity_lane(frozen: Mapping[str, Any], lane: str) -> dict[str, Any]:
    source = dict(frozen[lane])
    allowed = (
        (
            "authority",
            "base_url",
            "model",
            "structured_output_mode",
            "strict_json_schema",
        )
        if lane == "construction"
        else ("authority", "base_url", "model", "dimension", "dimension_policy")
    )
    return {field: source[field] for field in allowed}


async def _run_live(
    args: argparse.Namespace,
    *,
    preflight: Mapping[str, Any],
    journal: ObserverAttemptJournal,
) -> dict[str, Any]:
    from mab_quality_v2_final_qa.mab_main_dataset import build_authority

    frozen = load_strict_development_protocol(PROTOCOL)
    authority = build_authority(base.DATASET)
    if authority["local_file_sha256"] != frozen["workload"]["local_file_sha256"]:
        raise RuntimeError("strict development dataset hash differs from protocol")
    construction_key = os.environ.get("DASHSCOPE_API_KEY")
    embedding_key = os.environ.get("SILICONFLOW_API_KEY")
    if not construction_key or not embedding_key:
        raise RuntimeError(
            "DASHSCOPE_API_KEY and SILICONFLOW_API_KEY are required for --live"
        )
    environment = {
        **base._neo4j_environment(),
        "DASHSCOPE_API_KEY": construction_key,
        "SILICONFLOW_API_KEY": embedding_key,
        "GRAPHITI_MAX_COROUTINES": "8",
    }
    observations: list[dict[str, Any]] = []
    embedding_preflights: list[dict[str, Any]] = []
    completed = 0
    try:
        r12_spec = frozen["workload"]["r1_r2"]
        journal.record_progress(
            event="BLOCK_START",
            block_id="R1-R2",
            completed_block_count=completed,
        )
        r12 = await _run_block(
            run_id=args.run_id,
            block_id="R1-R2",
            spec=r12_spec,
            authority=authority,
            environment=environment,
            observations=observations,
            embedding_preflights=embedding_preflights,
        )
        completed += 1
        journal.record_progress(
            event="BLOCK_COMPLETE",
            block_id="R1-R2",
            completed_block_count=completed,
        )
        r1 = audit_r1_assumptions(r12)
        r2 = build_r2_causal_trace(r12)

        r3_blocks: list[dict[str, Any]] = []
        for spec in frozen["workload"]["r3_blocks"]:
            block_id = str(spec["block_id"])
            journal.record_progress(
                event="BLOCK_START",
                block_id=block_id,
                completed_block_count=completed,
            )
            block = await _run_block(
                run_id=args.run_id,
                block_id=block_id,
                spec=spec,
                authority=authority,
                environment=environment,
                observations=observations,
                embedding_preflights=embedding_preflights,
            )
            r3_blocks.append(block)
            completed += 1
            journal.record_progress(
                event="BLOCK_COMPLETE",
                block_id=block_id,
                completed_block_count=completed,
            )
        characterization = characterize_r3_blocks(
            r3_blocks,
            thresholds=frozen["thresholds"],
        )
        provider_summary = summarize_provider_observations(observations)
        construction_identity = _identity_lane(frozen, "construction")
        embedding_identity = _identity_lane(frozen, "embedding")
        identity = {
            "schema_version": "membind.v7.development-campaign-identity.v2",
            "run_id": args.run_id,
            "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
            "provider_identity_kind": STRICT_PROVIDER_IDENTITY_KIND,
            "protocol_sha256": preflight["protocol_sha256"],
            "strict_runtime_freeze_sha256": preflight[
                "strict_runtime_freeze_sha256"
            ],
            "source_methodology_protocol_sha256": frozen[
                "source_methodology_protocol"
            ]["sha256"],
            "scientific_method_selection_sha256": preflight[
                "scientific_method_selection_sha256"
            ],
            "construction": construction_identity,
            "embedding": embedding_identity,
            "backend": dict(frozen["backend"]),
            "workload": dict(frozen["workload"]),
            "thresholds": dict(frozen["thresholds"]),
            "observer_harness": {
                "schema_version": (
                    "membind.v7.strict-development-observer-harness-verification.v1"
                ),
                "status": "PASS",
                "source_sha256": dict(preflight["source_sha256"]),
            },
            "embedding_preflights": embedding_preflights,
            "provider_observation_summary": provider_summary,
            "formal_r1_r3_eligible": False,
            "live_treatment_authorized": False,
            "provider_swap_requires_new_formal_campaign": True,
            "treatment_calls": 0,
            "response_replay_calls": 0,
            "raw_request_persisted": False,
            "raw_response_persisted": False,
            "raw_embedding_persisted": False,
            "response_content_hashes_persisted": False,
            "credentials_recorded": False,
        }
        result = materialize_provider_development_artifacts(
            args.output,
            r1=r1,
            r2=r2,
            blocks=r3_blocks,
            characterization=characterization,
            campaign_identity=identity,
            expected_provider_identity_kind=STRICT_PROVIDER_IDENTITY_KIND,
            expected_construction_identity=construction_identity,
            expected_embedding_identity=embedding_identity,
            scientific_method_selection_path=base.METHOD_SELECTION,
            expected_scientific_method_selection_sha256=str(
                preflight["scientific_method_selection_sha256"]
            ),
        )
        record_provider_development_success(journal, result)
        return result
    finally:
        observations.clear()
        construction_key = ""
        embedding_key = ""
        environment.clear()


def _build_failure(**kwargs: Any) -> dict[str, Any]:
    error = kwargs["error"]
    return augment_development_failure(build_development_failure(**kwargs), error)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id",
        default="v7-development-strict-preflight",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if RUN_ID.fullmatch(args.run_id) is None:
        parser.error("--run-id must be a v7-development-strict-* non-path identity")
    if args.live and args.output is None:
        parser.error("--output is required with --live")
    if args.output is not None and args.output.exists():
        parser.error("--output must be fresh")

    preflight = _preflight()
    if not args.live:
        print(json.dumps(preflight, ensure_ascii=True, sort_keys=True))
        return 0

    protocol_sha256 = str(preflight["protocol_sha256"])
    method_sha256 = str(preflight["scientific_method_selection_sha256"])
    attempt_path = args.output.parent / f".{args.run_id}.attempt.jsonl"
    failure_path = args.output.parent / f".{args.run_id}.failure.json"
    journal = ObserverAttemptJournal.create(
        attempt_path,
        run_id=args.run_id,
        protocol_sha256=protocol_sha256,
        output_root_name=args.output.name,
    )
    try:
        result = asyncio.run(_run_live(args, preflight=preflight, journal=journal))
    except BaseException as error:
        completed = 0
        try:
            rows = attempt_path.read_text(encoding="ascii").splitlines()
            completed = max(
                (
                    int(row.get("completed_block_count", 0))
                    for row in (json.loads(line) for line in rows)
                    if isinstance(row, Mapping)
                ),
                default=0,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            completed = 0
        failure = _build_failure(
            run_id=args.run_id,
            error=error,
            protocol_sha256=protocol_sha256,
            scientific_method_selection_sha256=method_sha256,
            completed_block_count=completed,
        )
        try:
            journal.record_failure(
                failure={
                    "failure_class": failure["failure_class"],
                    "attempt_validity": "INVALID_FOR_R1_R3_GATES",
                    "replacement_eligible": failure["replacement_eligible"],
                    "gate_outcome": "NOT_EVALUATED",
                    "selected_method": None,
                },
                error_type=str(failure["error_type"]),
                error_message_sha256=str(failure["error_message_sha256"]),
                completed_block_count=completed,
            )
        finally:
            journal.close()
        base._write_exclusive(failure_path, failure)
        print(
            json.dumps(
                {
                    "status": "FAILED_CLOSED",
                    "run_id": args.run_id,
                    "failure_artifact": str(failure_path.resolve()),
                    "completed_block_count": completed,
                    "gate_outcome": "NOT_EVALUATED",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2
    journal.close()
    selected = result["development_method_selection"]
    print(
        json.dumps(
            {
                "status": result["status"],
                "run_id": args.run_id,
                "output": str(args.output.resolve()),
                "selected_method": selected["selected_method"],
                "implementation_authorized": selected["implementation_authorized"],
                "live_treatment_authorized": False,
                "formal_r1_r3_eligible": False,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
