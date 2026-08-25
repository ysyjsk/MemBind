#!/usr/bin/env python3
"""Run the preregistered real Graphiti V7 R1-R3 observer campaign.

The command accepts no API-key argument. A live invocation reads only
``SILICONFLOW_API_KEY`` from the process environment and maps it in memory to
the construction and embedding clients. Without ``--live`` this is a
provider-free protocol/dataset/pin preflight.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import (
    ObserverAttemptJournal,
    ObserverArtifactError,
    classify_observer_failure,
    load_protocol_freeze,
    run_real_observer_campaign_async,
    verify_observer_harness_sources,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.continuation import (
    ContinuationStatus,
    audit_continuation_source,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.pins import (
    GRAPHITI_VERSION,
    verify_membind_pin,
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bootstrap_sources(root: Path) -> None:
    for path in (
        root / "mab_quality_v2_final_qa/src",
        root / "membind-validation/src",
        root / "paper-eval-v3/src",
        root / "saturated_fixed_work_baseline_v1_2/src",
    ):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def _dataset(protocol: dict, dataset_path: Path):
    from mab_quality_v2_final_qa.mab_main_dataset import build_authority

    authority = build_authority(dataset_path)
    workload = protocol["workload"]
    if authority["local_file_sha256"] != workload["local_file_sha256"]:
        raise ObserverArtifactError("V7 observer dataset hash differs from protocol freeze")
    return authority


def _siliconflow_environment(root: Path) -> dict[str, str]:
    key = os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        raise ObserverArtifactError("SILICONFLOW_API_KEY is required for --live")
    from graphiti_native import load_env_file
    from mab_quality_v2_final_qa.runtime_gate import (
        SILICONFLOW_BASE_URL,
        SILICONFLOW_CHAT_MODEL,
        SILICONFLOW_EMBEDDING_DIMENSION,
        SILICONFLOW_EMBEDDING_MODEL,
        SILICONFLOW_PROVIDER,
    )

    local = load_env_file(root / "membind-validation/.env")
    neo4j_uri = os.environ.get("NEO4J_URI") or local.get("NEO4J_URI")
    neo4j_user = os.environ.get("NEO4J_USER") or local.get("NEO4J_USER")
    neo4j_password = os.environ.get("NEO4J_PASSWORD") or local.get("NEO4J_PASSWORD")
    if not neo4j_uri or not neo4j_user or not neo4j_password:
        raise ObserverArtifactError("Neo4j process environment is incomplete")
    return {
        "MAB_RUNTIME_PROVIDER": SILICONFLOW_PROVIDER,
        "CONSTRUCTION_LLM_API_KEY": key,
        "CONSTRUCTION_LLM_BASE_URL": SILICONFLOW_BASE_URL,
        "CONSTRUCTION_LLM_MODEL": SILICONFLOW_CHAT_MODEL,
        "QUALITY_LLM_BASE_URL": SILICONFLOW_BASE_URL,
        "QUALITY_LLM_MODEL": SILICONFLOW_CHAT_MODEL,
        "EMBEDDING_API_KEY": key,
        "EMBEDDING_BASE_URL": SILICONFLOW_BASE_URL,
        "EMBEDDING_MODEL": SILICONFLOW_EMBEDDING_MODEL,
        "EMBEDDING_DIM": str(SILICONFLOW_EMBEDDING_DIMENSION),
        "NEO4J_URI": neo4j_uri,
        "NEO4J_USER": neo4j_user,
        "NEO4J_PASSWORD": neo4j_password,
        "GRAPHITI_MAX_COROUTINES": "8",
    }


def _failure_digest(error: BaseException) -> str:
    return hashlib.sha256(str(error).encode("utf-8", errors="backslashreplace")).hexdigest()


def _write_exclusive_json(path: Path, value: dict[str, object]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False).encode("ascii")
        + b"\n"
    )
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("observer failure artifact write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    root = _repository_root()
    _bootstrap_sources(root)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        type=Path,
        default=root / "saturated_fixed_work_baseline_v1_3/v7/R1_R3_PROTOCOL_FREEZE.json",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=root / "mab_quality_v2_final_qa/data/official_5_contexts.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", default="v7-observer-preflight")
    parser.add_argument("--replacement-of")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    protocol = load_protocol_freeze(args.protocol)
    harness_verification = (
        verify_observer_harness_sources(root, protocol)
        if protocol.get("schema_version")
        in {
            "membind.v7.r1-r3-protocol-freeze.v3",
            "membind.v7.r1-r3-protocol-freeze.v4",
            "membind.v7.r1-r3-protocol-freeze.v5",
        }
        else {
            "schema_version": "membind.v7.observer-harness-verification.v1",
            "status": "UNBOUND_LEGACY_PROTOCOL",
            "source_sha256": {},
        }
    )
    authority = _dataset(protocol, args.dataset)
    import importlib.metadata
    import graphiti_core

    membind_pin = verify_membind_pin(root)
    graphiti_version = importlib.metadata.version("graphiti-core")
    graphiti_source = audit_continuation_source(Path(graphiti_core.__file__).resolve().parent)
    if (
        membind_pin["native_subject_match"] is not True
        or graphiti_version != GRAPHITI_VERSION
        or graphiti_source.status != ContinuationStatus.SUPPORTED_WITH_GUARD
    ):
        raise SystemExit("V7 observer pin verification failed")
    preflight = {
        "schema_version": "membind.v7.observer-preflight.v1",
        "status": "PASS",
        "live": bool(args.live),
        "provider_calls": 0,
        "context_count": authority["context_count"],
        "protocol_sha256": hashlib.sha256(args.protocol.read_bytes()).hexdigest(),
        "native_subject_match": True,
        "graphiti_pin_match": True,
        "observer_harness_bound": harness_verification["status"] == "PASS",
    }
    if not args.live:
        print(json.dumps(preflight, sort_keys=True))
        return 0
    if harness_verification["status"] != "PASS":
        raise SystemExit("V7 live observer requires a source-bound v3 protocol")
    if args.output is None:
        parser.error("--output is required with --live")
    if not args.run_id or args.run_id == "v7-observer-preflight":
        parser.error("an explicit unique --run-id is required with --live")
    if args.replacement_of == args.run_id:
        parser.error("a replacement must use a fresh run-id")

    env = _siliconflow_environment(root)
    from mab_quality_v2_final_qa.mab_main_dataset import build_episode_inputs
    from mab_quality_v2_final_qa.siliconflow_runtime import (
        REQUESTED_MAX_TOKENS,
        SILICONFLOW_HTTP_TIMEOUT_SECONDS,
        build_siliconflow_u0_runtime,
    )
    from native_characterization_instrumentation import (
        install_native_characterization_instrumentation,
    )
    from native_characterization_tracing import TraceRecorder
    from saturated_fixed_work_baseline_v1_3.membind_v7.graphiti_observer import (
        current_provider_observation_scope,
    )

    provider = protocol.get("provider")
    if not isinstance(provider, dict):
        raise ObserverArtifactError("V7 provider configuration is incomplete")
    requested_max_tokens = int(
        provider.get("requested_max_tokens", REQUESTED_MAX_TOKENS)
    )
    http_timeout_seconds = float(
        provider.get("http_timeout_seconds", SILICONFLOW_HTTP_TIMEOUT_SECONDS)
    )
    if provider.get("sdk_max_retries", 0) != 0 or provider.get(
        "hard_attempt_limit_per_request", 1
    ) != 1:
        raise ObserverArtifactError("V7 provider transport retry policy is invalid")

    def runtime_builder_factory(lane: str):
        def record_provider_response(value: dict[str, object]) -> None:
            scope = current_provider_observation_scope() or {}
            journal.record_provider_response(
                lane=lane,
                finish_reason=(
                    str(value["finish_reason"])
                    if value.get("finish_reason") is not None
                    else None
                ),
                prompt_tokens=int(value["prompt_tokens"]),
                completion_tokens=int(value["completion_tokens"]),
                content_bytes=int(value["content_bytes"]),
                content_sha256=str(value["content_sha256"]),
                phase=str(scope["phase"]) if scope.get("phase") is not None else None,
                source_sequence=(
                    int(scope["source_sequence"])
                    if scope.get("source_sequence") is not None
                    else None
                ),
                request_ordinal=(
                    int(scope["request_ordinal"])
                    if scope.get("request_ordinal") is not None
                    else None
                ),
                prompt_name=(
                    str(scope["prompt_name"])
                    if scope.get("prompt_name") is not None
                    else None
                ),
            )

        return lambda: build_siliconflow_u0_runtime(
            env=env,
            request_id_prefix=f"membind-v7:{args.run_id}:{lane}",
            requested_max_tokens=requested_max_tokens,
            http_timeout_seconds=http_timeout_seconds,
            response_observer=record_provider_response,
        )

    protocol_sha256 = hashlib.sha256(args.protocol.read_bytes()).hexdigest()
    journal = ObserverAttemptJournal.create(
        args.output.parent / f".{args.output.name}.attempt.jsonl",
        run_id=args.run_id,
        protocol_sha256=protocol_sha256,
        output_root_name=args.output.name,
        replacement_of=args.replacement_of,
    )
    progress_state = {"completed_block_count": 0}

    def record_progress(row: dict[str, object]) -> None:
        event = str(row["event"])
        block_id = str(row["block_id"])
        completed = int(row["completed_block_count"])
        journal.record_progress(
            event=event,
            block_id=block_id,
            completed_block_count=completed,
        )
        if event == "BLOCK_COMPLETE":
            progress_state["completed_block_count"] = completed

    try:
        result = asyncio.run(
            run_real_observer_campaign_async(
                protocol=protocol,
                contexts=authority["contexts"],
                episode_builder=build_episode_inputs,
                runtime_builder_factory=runtime_builder_factory,
                output_root=args.output,
                run_id=args.run_id,
                recorder_factory=TraceRecorder,
                instrumentation_installer=install_native_characterization_instrumentation,
                progress_observer=record_progress,
                observer_harness_verification=harness_verification,
            )
        )
    except BaseException as error:
        classification = classify_observer_failure(error)
        error_type = f"{type(error).__module__}.{type(error).__qualname__}"
        error_digest = _failure_digest(error)
        journal.record_failure(
            failure=classification,
            error_type=error_type,
            error_message_sha256=error_digest,
            completed_block_count=progress_state["completed_block_count"],
        )
        journal.close()
        failure = {
            "schema_version": "membind.v7.observer-failure.v2",
            "status": "INVALID_ATTEMPT",
            "run_id": args.run_id,
            "replacement_of": args.replacement_of,
            "replacement_contract": "fresh_run_id_and_fresh_namespace_required",
            "output_root_name": args.output.name,
            "protocol_sha256": protocol_sha256,
            "attempt_journal": journal.path.name,
            "attempt_journal_sha256": journal.sha256,
            "completed_block_count": progress_state["completed_block_count"],
            "error_type": error_type,
            "error_message_sha256": error_digest,
            "provider_key_recorded": False,
            "raw_request_recorded": False,
            "raw_response_recorded": False,
            "treatment_calls": 0,
            "response_replay_calls": 0,
            **classification,
        }
        failure_path = args.output.parent / f".{args.output.name}.failure.json"
        try:
            _write_exclusive_json(failure_path, failure)
        except OSError as artifact_error:
            print(
                json.dumps(
                    {
                        "schema_version": "membind.v7.observer-failure-persistence.v1",
                        "status": "FAILURE_ARTIFACT_WRITE_FAILED",
                        "run_id": args.run_id,
                        "error_type": f"{type(artifact_error).__module__}.{type(artifact_error).__qualname__}",
                        "provider_key_recorded": False,
                    },
                    sort_keys=True,
                )
            )
            return 2
        print(json.dumps(failure, sort_keys=True))
        return 1
    journal.record_success(manifest_sha256=result["manifest_sha256"])
    journal.close()
    print(
        json.dumps(
            {
                "status": result["status"],
                "method": result["method_selection"]["selected_method"],
                "authorized": result["method_selection"]["authorized"],
                "manifest_sha256": result["manifest_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
