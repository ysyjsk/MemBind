#!/usr/bin/env python3
"""Run the frozen Bailian/SiliconFlow V7 2+6+6 development campaign."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "saturated_fixed_work_baseline_v1_3"
for source in (
    PROJECT / "src",
    ROOT / "mab_quality_v2_final_qa/src",
    ROOT / "membind-validation/src",
    ROOT / "paper-eval-v3/src",
):
    selected = str(source)
    if selected not in sys.path:
        sys.path.insert(0, selected)

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
    load_development_protocol,
    materialize_development_artifacts,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.engineering_observer_runtime import (  # noqa: E402
    build_composite_engineering_runtime,
    build_embedding_preflight_evidence,
    summarize_provider_observations,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import (  # noqa: E402
    ObserverAttemptJournal,
    run_graphiti_observer_block_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.pins import (  # noqa: E402
    GRAPHITI_VERSION,
    verify_membind_pin,
)


PROTOCOL = PROJECT / "v7/BAILIAN_SILICONFLOW_V7_DEVELOPMENT_PROTOCOL.json"
COMPOSITE_FREEZE = (
    PROJECT / "v7/BAILIAN_SILICONFLOW_ENGINEERING_OBSERVER_FREEZE.json"
)
DATASET = ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json"
METHOD_SELECTION = PROJECT / "v7/METHOD_SELECTION.json"
RUN_ID = re.compile(r"v7-development-[a-z0-9][a-z0-9-]{2,79}")


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("development failure artifact write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _source_bindings() -> dict[str, str]:
    import graphiti_core

    package = Path(graphiti_core.__file__).resolve().parent
    paths = {
        "mab_quality_v2_final_qa/src/mab_quality_v2_final_qa/mab_main_dataset.py": (
            ROOT
            / "mab_quality_v2_final_qa/src/mab_quality_v2_final_qa/mab_main_dataset.py"
        ),
        "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/embedder/openai.py": (
            package / "embedder/openai.py"
        ),
        "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/graphiti.py": (
            package / "graphiti.py"
        ),
        "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/llm_client/openai_generic_client.py": (
            package / "llm_client/openai_generic_client.py"
        ),
        "membind-validation/src/graphiti_native.py": (
            ROOT / "membind-validation/src/graphiti_native.py"
        ),
        "membind-validation/src/native_characterization_instrumentation.py": (
            ROOT / "membind-validation/src/native_characterization_instrumentation.py"
        ),
        "membind-validation/src/native_characterization_tracing.py": (
            ROOT / "membind-validation/src/native_characterization_tracing.py"
        ),
        "saturated_fixed_work_baseline_v1_3/scripts/run_v7_composite_development_campaign.py": (
            Path(__file__).resolve()
        ),
    }
    for name in (
        "certificates.py",
        "characterization.py",
        "continuation.py",
        "development_campaign.py",
        "engineering_observer_runtime.py",
        "gates.py",
        "graphiti_observer.py",
        "observer_campaign.py",
        "opportunity.py",
        "state_delta.py",
    ):
        paths[
            "saturated_fixed_work_baseline_v1_3/src/"
            f"saturated_fixed_work_baseline_v1_3/membind_v7/{name}"
        ] = PROJECT / f"src/saturated_fixed_work_baseline_v1_3/membind_v7/{name}"
    return {name: _sha256(path) for name, path in sorted(paths.items())}


def _neo4j_environment() -> dict[str, str]:
    from graphiti_native import load_env_file

    local = load_env_file(ROOT / "membind-validation/.env")
    result: dict[str, str] = {}
    for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        value = os.environ.get(name) or local.get(name)
        if not value:
            raise RuntimeError(f"development Neo4j environment is missing: {name}")
        result[name] = value
    return result


async def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


def _selected_episodes(
    authority: Mapping[str, Any], spec: Mapping[str, Any]
) -> tuple[Any, ...]:
    from mab_quality_v2_final_qa.mab_main_dataset import build_episode_inputs

    context_index = int(spec["context_index"])
    episodes = tuple(build_episode_inputs(authority["contexts"][context_index]))
    start = int(spec["source_start"])
    count = int(spec["source_count"])
    selected = episodes[start : start + count]
    if len(selected) != count or [item.source_sequence for item in selected] != list(
        range(count)
    ):
        raise RuntimeError("development workload prefix is invalid")
    return selected


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
        runtime = build_composite_engineering_runtime(
            env=environment,
            request_id_prefix=f"membind-v7:{run_id}:{block_id.casefold()}",
            composite_freeze_path=COMPOSITE_FREEZE,
            response_observer=lambda row: observations.append(dict(row)),
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
            episodes=_selected_episodes(authority, spec),
            runtime_builder=runtime_builder,
            recorder_factory=TraceRecorder,
            instrumentation_installer=install_native_characterization_instrumentation,
        )
    finally:
        runtime = holder.get("runtime")
        if runtime is not None:
            await _close(runtime.construction_transport)
            await _close(runtime.embedding_transport)


def _preflight() -> dict[str, Any]:
    import graphiti_core

    frozen = load_development_protocol(PROTOCOL)
    source_sha256 = _source_bindings()
    protocol_sources = frozen["observer_harness"]["source_sha256"]
    pin = verify_membind_pin(ROOT)
    continuation = audit_continuation_source(
        Path(graphiti_core.__file__).resolve().parent
    )
    if (
        _sha256(DATASET) != frozen["workload"]["local_file_sha256"]
        or source_sha256 != protocol_sources
        or pin["native_subject_match"] is not True
        or importlib.metadata.version("graphiti-core") != GRAPHITI_VERSION
        or continuation.status != ContinuationStatus.SUPPORTED_WITH_GUARD
        or _sha256(METHOD_SELECTION)
        != frozen["scientific_method_selection"]["sha256"]
    ):
        raise RuntimeError("V7 development campaign preflight failed")
    return {
        "schema_version": "membind.v7.development-preflight.v1",
        "status": "PASS",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "protocol_sha256": _sha256(PROTOCOL),
        "composite_provider_freeze_sha256": _sha256(COMPOSITE_FREEZE),
        "dataset_sha256": _sha256(DATASET),
        "scientific_method_selection_sha256": _sha256(METHOD_SELECTION),
        "source_sha256": source_sha256,
        "provider_calls": 0,
        "embedding_calls": 0,
        "database_calls": 0,
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
    }


async def _run_live(
    args: argparse.Namespace,
    *,
    preflight: Mapping[str, Any],
    journal: ObserverAttemptJournal,
) -> dict[str, Any]:
    from mab_quality_v2_final_qa.mab_main_dataset import build_authority

    frozen = load_development_protocol(PROTOCOL)
    authority = build_authority(DATASET)
    if authority["local_file_sha256"] != frozen["workload"]["local_file_sha256"]:
        raise RuntimeError("development dataset hash differs from protocol")
    construction_key = os.environ.get("DASHSCOPE_API_KEY")
    embedding_key = os.environ.get("SILICONFLOW_API_KEY")
    if not construction_key or not embedding_key:
        raise RuntimeError(
            "DASHSCOPE_API_KEY and SILICONFLOW_API_KEY are required for --live"
        )
    environment = {
        **_neo4j_environment(),
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
        identity = {
            "schema_version": "membind.v7.development-campaign-identity.v1",
            "run_id": args.run_id,
            "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
            "provider_identity_kind": "COMPOSITE_ENGINEERING_ONLY",
            "protocol_sha256": preflight["protocol_sha256"],
            "composite_provider_freeze_sha256": preflight[
                "composite_provider_freeze_sha256"
            ],
            "source_methodology_protocol_sha256": frozen[
                "source_methodology_protocol"
            ]["sha256"],
            "scientific_method_selection_sha256": preflight[
                "scientific_method_selection_sha256"
            ],
            "construction": {
                "authority": frozen["construction"]["authority"],
                "base_url": frozen["construction"]["base_url"],
                "model": frozen["construction"]["model"],
                "structured_output_mode": frozen["construction"][
                    "structured_output_mode"
                ],
            },
            "embedding": {
                "authority": frozen["embedding"]["authority"],
                "base_url": frozen["embedding"]["base_url"],
                "model": frozen["embedding"]["model"],
                "dimension": frozen["embedding"]["dimension"],
                "dimension_policy": frozen["embedding"]["dimension_policy"],
            },
            "backend": dict(frozen["backend"]),
            "workload": dict(frozen["workload"]),
            "thresholds": dict(frozen["thresholds"]),
            "observer_harness": {
                "schema_version": "membind.v7.development-observer-harness-verification.v1",
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
            "credentials_recorded": False,
        }
        result = materialize_development_artifacts(
            args.output,
            r1=r1,
            r2=r2,
            blocks=r3_blocks,
            characterization=characterization,
            campaign_identity=identity,
            scientific_method_selection_path=METHOD_SELECTION,
            expected_scientific_method_selection_sha256=str(
                preflight["scientific_method_selection_sha256"]
            ),
        )
        journal.record_progress(
            event="CAMPAIGN_SEALED",
            block_id=None,
            completed_block_count=completed,
        )
        return result
    finally:
        observations.clear()
        construction_key = ""
        embedding_key = ""
        environment.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id", default="v7-development-composite-preflight"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if RUN_ID.fullmatch(args.run_id) is None:
        parser.error("--run-id must be a v7-development-* non-path identity")
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
        failure = build_development_failure(
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
        _write_exclusive(failure_path, failure)
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
