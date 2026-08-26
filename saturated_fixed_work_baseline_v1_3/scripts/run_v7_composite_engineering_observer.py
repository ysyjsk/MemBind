#!/usr/bin/env python3
"""Run the frozen Bailian/SiliconFlow V7 engineering observer workload."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


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

from saturated_fixed_work_baseline_v1_3.membind_v7.continuation import (  # noqa: E402
    ContinuationStatus,
    audit_continuation_source,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.engineering_observer_runtime import (  # noqa: E402
    build_composite_engineering_runtime,
    build_embedding_preflight_evidence,
    build_engineering_observer_artifact,
    load_composite_engineering_freeze,
    summarize_provider_observations,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import (  # noqa: E402
    run_graphiti_observer_block_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.pins import (  # noqa: E402
    GRAPHITI_VERSION,
    verify_membind_pin,
)


FREEZE = PROJECT / "v7/BAILIAN_SILICONFLOW_ENGINEERING_OBSERVER_FREEZE.json"
DATASET = ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json"
METHOD_SELECTION = PROJECT / "v7/METHOD_SELECTION.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_exclusive(path: Path, value: dict[str, Any]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("engineering artifact write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _source_bindings() -> dict[str, str]:
    import graphiti_core

    paths = {
        "mab_quality_v2_final_qa/src/mab_quality_v2_final_qa/mab_main_dataset.py": (
            ROOT / "mab_quality_v2_final_qa/src/mab_quality_v2_final_qa/mab_main_dataset.py"
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
        "saturated_fixed_work_baseline_v1_3/scripts/run_v7_composite_engineering_observer.py": (
            Path(__file__).resolve()
        ),
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/engineering_observer_runtime.py": (
            PROJECT
            / "src/saturated_fixed_work_baseline_v1_3/membind_v7/engineering_observer_runtime.py"
        ),
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/graphiti_observer.py": (
            PROJECT
            / "src/saturated_fixed_work_baseline_v1_3/membind_v7/graphiti_observer.py"
        ),
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/observer_campaign.py": (
            PROJECT
            / "src/saturated_fixed_work_baseline_v1_3/membind_v7/observer_campaign.py"
        ),
        "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/graphiti.py": (
            Path(graphiti_core.__file__).resolve().parent / "graphiti.py"
        ),
        "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/llm_client/openai_generic_client.py": (
            Path(graphiti_core.__file__).resolve().parent
            / "llm_client/openai_generic_client.py"
        ),
        "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/embedder/openai.py": (
            Path(graphiti_core.__file__).resolve().parent / "embedder/openai.py"
        ),
    }
    return {name: _sha256(path) for name, path in sorted(paths.items())}


def _neo4j_environment() -> dict[str, str]:
    from graphiti_native import load_env_file

    local = load_env_file(ROOT / "membind-validation/.env")
    result: dict[str, str] = {}
    for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
        value = os.environ.get(name) or local.get(name)
        if not value:
            raise RuntimeError(f"engineering Neo4j environment is missing: {name}")
        result[name] = value
    return result


def _safe_failure(
    *, run_id: str, error: BaseException, method_sha256: str
) -> dict[str, Any]:
    message = str(error).encode("utf-8", errors="backslashreplace")
    current_method_sha256 = _sha256(METHOD_SELECTION)
    return {
        "schema_version": "membind.v7.composite-engineering-observer-failure.v1",
        "status": "FAIL",
        "mode": "ENGINEERING_OBSERVER",
        "run_id": run_id,
        "provider_identity_kind": "COMPOSITE_ENGINEERING_ONLY",
        "error_type": f"{type(error).__module__}.{type(error).__qualname__}",
        "error_message_sha256": hashlib.sha256(message).hexdigest(),
        "formal_r1_r3_eligible": False,
        "gate_a_e_evaluated": False,
        "gates": {name: "NOT_EVALUATED" for name in "ABCDE"},
        "gate_outcome": "NOT_EVALUATED",
        "selected_method": None,
        "treatment_authorized": False,
        "treatment_calls": 0,
        "response_replay_calls": 0,
        "scientific_method_selection_updated": current_method_sha256 != method_sha256,
        "method_selection_sha256_before": method_sha256,
        "method_selection_sha256_after": current_method_sha256,
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "raw_embedding_persisted": False,
        "credentials_recorded": False,
    }


async def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


async def _run_live(args: argparse.Namespace) -> dict[str, Any]:
    from mab_quality_v2_final_qa.mab_main_dataset import (
        build_authority,
        build_episode_inputs,
    )
    from native_characterization_instrumentation import (
        install_native_characterization_instrumentation,
    )
    from native_characterization_tracing import TraceRecorder

    frozen = load_composite_engineering_freeze(FREEZE)
    authority = build_authority(DATASET)
    workload = frozen["workload"]
    if authority["local_file_sha256"] != workload["local_file_sha256"]:
        raise RuntimeError("engineering observer dataset hash differs from freeze")
    episodes = tuple(
        build_episode_inputs(authority["contexts"][int(workload["context_index"])])
    )
    start = int(workload["source_start"])
    count = int(workload["source_count"])
    selected = episodes[start : start + count]
    if len(selected) != count or [item.source_sequence for item in selected] != [0, 1]:
        raise RuntimeError("engineering observer workload prefix is invalid")

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
    runtime_holder: dict[str, Any] = {}
    embedding_evidence: dict[str, Any] = {}

    async def runtime_builder() -> Any:
        runtime = build_composite_engineering_runtime(
            env=environment,
            request_id_prefix=f"membind-v7:{args.run_id}:engineering",
            composite_freeze_path=FREEZE,
            response_observer=lambda row: observations.append(dict(row)),
        )
        runtime_holder["runtime"] = runtime
        before_ns = time.monotonic_ns()
        vector = await runtime.embedder.create(
            "MemBind V7 composite engineering embedding dimension preflight."
        )
        embedding_evidence.update(
            build_embedding_preflight_evidence(
                duration_ns=time.monotonic_ns() - before_ns,
                vector=vector,
            )
        )
        vector = []
        return runtime

    try:
        block = await run_graphiti_observer_block_async(
            run_id=args.run_id,
            block_id="ENGINEERING-2S",
            namespace=f"membind-v7-{args.run_id}-engineering-2s".casefold(),
            episodes=selected,
            runtime_builder=runtime_builder,
            recorder_factory=TraceRecorder,
            instrumentation_installer=install_native_characterization_instrumentation,
        )
        summary = summarize_provider_observations(observations)
        return build_engineering_observer_artifact(
            run_id=args.run_id,
            composite_freeze_path=FREEZE,
            block_result=block,
            source_sha256=_source_bindings(),
            method_selection_path=METHOD_SELECTION,
            embedding_preflight=embedding_evidence,
            provider_observation_summary=summary,
        )
    finally:
        runtime = runtime_holder.get("runtime")
        if runtime is not None:
            await _close(runtime.construction_transport)
            await _close(runtime.embedding_transport)
        construction_key = ""
        embedding_key = ""
        environment.clear()


def _preflight() -> dict[str, Any]:
    import graphiti_core

    frozen = load_composite_engineering_freeze(FREEZE)
    authority_sha256 = _sha256(DATASET)
    pin = verify_membind_pin(ROOT)
    graphiti_source = audit_continuation_source(Path(graphiti_core.__file__).resolve().parent)
    if (
        authority_sha256 != frozen["workload"]["local_file_sha256"]
        or pin["native_subject_match"] is not True
        or importlib.metadata.version("graphiti-core") != GRAPHITI_VERSION
        or graphiti_source.status != ContinuationStatus.SUPPORTED_WITH_GUARD
        or _sha256(METHOD_SELECTION)
        != frozen["scientific_method_selection"]["sha256"]
    ):
        raise RuntimeError("composite engineering observer preflight failed")
    return {
        "schema_version": "membind.v7.composite-engineering-preflight.v1",
        "status": "PASS",
        "provider_identity_kind": "COMPOSITE_ENGINEERING_ONLY",
        "dataset_sha256": authority_sha256,
        "native_subject_match": True,
        "graphiti_pin_match": True,
        "method_selection_sha256": _sha256(METHOD_SELECTION),
        "source_sha256": _source_bindings(),
        "provider_calls": 0,
        "embedding_calls": 0,
        "database_calls": 0,
        "formal_r1_r3_eligible": False,
        "gate_outcome": "NOT_EVALUATED",
        "treatment_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="v7-composite-engineering-preflight")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.run_id or "/" in args.run_id or "\\" in args.run_id:
        parser.error("--run-id must be a non-path identity")
    if args.live and args.output is None:
        parser.error("--output is required with --live")
    if args.output is not None and args.output.exists():
        parser.error("--output must be fresh")

    preflight = _preflight()
    if not args.live:
        print(json.dumps(preflight, ensure_ascii=True, sort_keys=True))
        return 0
    method_sha256 = _sha256(METHOD_SELECTION)
    try:
        result = asyncio.run(_run_live(args))
    except BaseException as error:
        result = _safe_failure(
            run_id=args.run_id,
            error=error,
            method_sha256=method_sha256,
        )
        _write_exclusive(args.output, result)
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "run_id": args.run_id,
                    "output": str(args.output.resolve()),
                    "error_type": result["error_type"],
                    "formal_r1_r3_eligible": False,
                    "gate_outcome": "NOT_EVALUATED",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2
    _write_exclusive(args.output, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "run_id": result["run_id"],
                "output": str(args.output.resolve()),
                "formal_r1_r3_eligible": False,
                "gate_outcome": "NOT_EVALUATED",
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
