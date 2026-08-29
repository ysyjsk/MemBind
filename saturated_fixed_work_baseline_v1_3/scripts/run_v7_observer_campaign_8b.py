#!/usr/bin/env python3
"""Run the frozen V7 observer protocol on the matched local 8B platform.

This runner is observer-only: it executes the existing Graphiti construction
seam, records old-build/fresh-publication evidence, and never invokes a V7
treatment or response replay.  It is intentionally separate from the legacy
SiliconFlow runner so provider identity and resource binding are explicit.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "mab_quality_v2_final_qa/src",
    ROOT / "saturated_fixed_work_baseline_v1_3/src",
    ROOT / "paper-eval-v3/src",
    ROOT / "membind-validation/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.mab_main_dataset import build_authority, build_episode_inputs  # noqa: E402
from saturated_fixed_work_baseline_v1_3.mab_live_runner import episode_from_input  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime_8b import (  # noqa: E402
    build_8b_u0_runtime,
    load_8b_routing_contract,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.continuation import (  # noqa: E402
    ContinuationStatus,
    audit_continuation_source,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import (  # noqa: E402
    ObserverAttemptJournal,
    ObserverArtifactError,
    classify_observer_failure,
    load_protocol_freeze,
    run_real_observer_campaign_async,
    verify_observer_harness_sources,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.pins import (  # noqa: E402
    GRAPHITI_VERSION,
    verify_membind_pin,
)


def _failure_digest(error: BaseException) -> str:
    return hashlib.sha256(
        str(error).encode("utf-8", errors="backslashreplace")
    ).hexdigest()


def _write_exclusive_json(path: Path, value: dict[str, Any]) -> None:
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


def _dataset(protocol: dict[str, Any], path: Path) -> dict[str, Any]:
    authority = build_authority(path)
    expected = protocol["workload"]["local_file_sha256"]
    if authority["local_file_sha256"] != expected:
        raise ObserverArtifactError("V7 observer dataset hash differs from protocol freeze")
    return authority


def _preflight(protocol: dict[str, Any]) -> dict[str, Any]:
    harness = verify_observer_harness_sources(ROOT, protocol)
    pin = verify_membind_pin(ROOT)
    if pin["native_subject_match"] is not True:
        raise ObserverArtifactError("native subject pin verification failed")
    import importlib.metadata
    import graphiti_core

    graphiti_version = importlib.metadata.version("graphiti-core")
    source = audit_continuation_source(Path(graphiti_core.__file__).resolve().parent)
    if graphiti_version != GRAPHITI_VERSION or source.status != ContinuationStatus.SUPPORTED_WITH_GUARD:
        raise ObserverArtifactError("Graphiti continuation pin verification failed")
    return {
        "schema_version": "membind.v7.local-8b-observer-preflight.v1",
        "status": "PASS",
        "profile_id": os.environ.get("MEMBIND_PROFILE_ID"),
        "graphiti_version": graphiti_version,
        "native_subject_match": True,
        "observer_harness_bound": harness["status"] == "PASS",
        "provider_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "saturated_fixed_work_baseline_v1_3/v7/R1_R3_PROTOCOL_FREEZE_8B_DUAL_V1.json",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", default="v7-observer-preflight")
    parser.add_argument("--replacement-of")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    if os.environ.get("MEMBIND_PROFILE_ID") != "local-qwen3-8b-awq-dualreplica-v1":
        raise SystemExit("activate scripts/local_runtime_8b_dual/activate.sh first")
    protocol = load_protocol_freeze(args.protocol)
    authority = _dataset(protocol, args.dataset)
    preflight = _preflight(protocol)
    if not args.live:
        print(json.dumps(preflight, sort_keys=True))
        return 0
    if args.output is None:
        parser.error("--output is required with --live")
    if not args.run_id or args.run_id == "v7-observer-preflight":
        parser.error("an explicit unique --run-id is required with --live")
    if args.replacement_of == args.run_id:
        parser.error("a replacement must use a fresh run-id")

    routes = load_8b_routing_contract(os.environ["MEMBIND_NATIVE_ROUTING_CONFIG"])
    route_events: list[dict[str, Any]] = []

    def runtime_builder_factory(_lane: str):
        def build() -> Any:
            runtime = build_8b_u0_runtime(
                routing_contract=routes,
                route_event_sink=route_events.append,
            )
            from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import (  # noqa: PLC0415
                provider_scope,
            )
            from saturated_fixed_work_baseline_v1_3.membind_v7.graphiti_observer import (  # noqa: PLC0415
                current_provider_observation_scope,
            )
            original_generate = runtime.llm_client.generate_response

            async def scoped_generate(*args: Any, **kwargs: Any) -> Any:
                observation = current_provider_observation_scope() or {}
                sequence = observation.get("source_sequence")
                phase = observation.get("phase")
                if isinstance(sequence, int) and phase in {"OLD", "FRESH_NATIVE"}:
                    region = "PREPARE" if phase == "OLD" else "NATIVE"
                    with provider_scope(region=region, source_sequence=sequence):
                        return await original_generate(*args, **kwargs)
                return await original_generate(*args, **kwargs)

            runtime.llm_client.generate_response = scoped_generate
            runtime._membind_observer_generate_restore = lambda: setattr(
                runtime.llm_client, "generate_response", original_generate
            )
            # The observer block owns graphiti.close.  Wrap that call locally
            # so each block also releases the two AsyncOpenAI transports and
            # restores local adapters, without changing the frozen observer
            # module or its source hash.
            graphiti = runtime.graphiti
            original_close = graphiti.close
            closed = False

            async def close_all() -> None:
                nonlocal closed
                if closed:
                    return
                for name in (
                    "_membind_context_budget_restore",
                    "_membind_route_prompt_restore",
                    "_membind_semantic_shortcut_restore",
                    "_membind_candidate_provenance_restore",
                ):
                    restore = getattr(runtime, name, None)
                    if callable(restore):
                        restore()
                        setattr(runtime, name, None)
                restore_generate = getattr(runtime, "_membind_observer_generate_restore", None)
                if callable(restore_generate):
                    restore_generate()
                    runtime._membind_observer_generate_restore = None
                await original_close()
                for transport in getattr(runtime, "_membind_owned_transports", ()):
                    close = getattr(transport, "close", None)
                    if callable(close):
                        pending = close()
                        if hasattr(pending, "__await__"):
                            await pending
                closed = True

            graphiti.close = close_all
            return runtime

        return build

    def episode_builder(context: Any) -> tuple[Any, ...]:
        return tuple(episode_from_input(item) for item in build_episode_inputs(context))

    protocol_sha256 = hashlib.sha256(args.protocol.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    journal = ObserverAttemptJournal.create(
        args.output.parent / f".{args.output.name}.attempt.jsonl",
        run_id=args.run_id,
        protocol_sha256=protocol_sha256,
        output_root_name=args.output.name,
        replacement_of=args.replacement_of,
    )
    progress_state = {"completed_block_count": 0}

    def progress(row: dict[str, Any]) -> None:
        journal.record_progress(
            event=str(row["event"]),
            block_id=str(row["block_id"]),
            completed_block_count=int(row["completed_block_count"]),
        )
        if row["event"] == "BLOCK_COMPLETE":
            progress_state["completed_block_count"] = int(row["completed_block_count"])

    try:
        from native_characterization_instrumentation import (  # noqa: PLC0415
            install_native_characterization_instrumentation,
        )
        from native_characterization_tracing import TraceRecorder  # noqa: PLC0415

        result = asyncio.run(
            run_real_observer_campaign_async(
                protocol=protocol,
                contexts=authority["contexts"],
                episode_builder=episode_builder,
                runtime_builder_factory=runtime_builder_factory,
                output_root=args.output,
                run_id=args.run_id,
                recorder_factory=TraceRecorder,
                instrumentation_installer=install_native_characterization_instrumentation,
                progress_observer=progress,
                observer_harness_verification=preflight | {"source_sha256": protocol["observer_harness"]["source_sha256"]},
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
            "schema_version": "membind.v7.local-8b-observer-failure.v1",
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
        _write_exclusive_json(args.output.parent / f".{args.output.name}.failure.json", failure)
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
                "route_event_count": len(route_events),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
