#!/usr/bin/env python3
"""Run the isolated MemBind-v1 aligned development table.

The command owns only new ``paper_eval`` artifact roots.  It freezes one
source/arrival/runtime identity, executes the twelve fresh blocks in plan
order, and reduces them only after every block has a verified public row.
Long executions should be launched through ``run_membind_v1_tmux.sh``.
"""

import argparse
import asyncio
import fcntl
import hashlib
import inspect
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
LEGACY = PROJECT.parent / "membind-validation"
DATASET = Path("/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json")
MEMBIND_RUNS_ROOT = PROJECT / "artifacts/paper_eval/membind_v1/runs"
ALIGNED_TABLE_RUNS_ROOT = PROJECT / "artifacts/paper_eval/aligned_main_table/runs"
FROZEN_INTERARRIVAL_NS = 41_811_191_012
HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")

# Keep path admission identical to the two downstream frozen contracts.  The
# command validates these before it creates either new-lane artifact root.
_ALIGNED_RUN_ID = re.compile(r"^aligned-[a-z0-9][a-z0-9-]{2,63}$")
_MAIN_TABLE_RUN_ID = re.compile(r"^main-table-[a-z0-9][a-z0-9-]{2,63}$")

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(LEGACY / "src") not in sys.path:
    sys.path.insert(0, str(LEGACY / "src"))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.membind_v1.aligned_metrics import derive_aligned_block_output
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_DEVELOPMENT_HISTORIES,
    build_aligned_development_plan,
    verify_aligned_development_plan,
)
from paper_eval.membind_v1.aligned_quality_live import observe_aligned_quality_live
from paper_eval.membind_v1.aligned_quality_production import (
    build_aligned_quality_hooks,
    build_read_only_aligned_quality_runtime,
    close_read_only_aligned_quality_runtime,
)
from paper_eval.membind_v1.aligned_reduce import reduce_aligned_blocks
from paper_eval.membind_v1.aligned_live import execute_aligned_live_block
from paper_eval.membind_v1.execution_identity import build_node_artifact_identity
from paper_eval.membind_v1.graphiti_adapter import NodeArtifactIdentity
from paper_eval.membind_v1.live_runtime import (
    build_membind_v1_runtime,
    project_membind_v1_runtime_identity,
)
from paper_eval.membind_v1.smoke import (
    inspect_membind_v1_smoke,
    run_membind_v1_smoke,
)
from paper_eval.membind_v1.main_table import (
    bind_sealed_historical_references,
    build_development_main_table,
    render_development_main_table_markdown,
)


class RunnerError(RuntimeError):
    """The aligned command cannot safely continue or merge a block."""


def _qualified_error(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RunnerError(f"artifact unreadable: {path.name}") from None
    if not isinstance(value, dict):
        raise RunnerError(f"artifact invalid: {path.name}")
    return value


def _safe_write(path: Path, value: Mapping[str, object]) -> None:
    atomic_write_json(path, dict(value))


def _source_hashes(workload: Mapping[str, Mapping[str, object]]) -> dict[str, list[str]]:
    if tuple(workload) != ALIGNED_DEVELOPMENT_HISTORIES:
        raise RunnerError("development workload history inventory drift")
    result: dict[str, list[str]] = {}
    for history_id in ALIGNED_DEVELOPMENT_HISTORIES:
        entry = workload.get(history_id)
        if not isinstance(entry, Mapping):
            raise RunnerError("development workload entry invalid")
        episodes = entry.get("episodes")
        if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence) or not episodes:
            raise RunnerError("development episode inventory invalid")
        hashes: list[str] = []
        for sequence, episode in enumerate(episodes):
            if getattr(episode, "source_sequence", None) != sequence:
                raise RunnerError("development source sequence is not contiguous")
            source_hash = getattr(episode, "source_hash", None)
            if not isinstance(source_hash, str) or len(source_hash) != 64:
                raise RunnerError("development source identity invalid")
            try:
                int(source_hash, 16)
            except ValueError:
                raise RunnerError("development source identity invalid") from None
            if source_hash in hashes:
                raise RunnerError("development source identity duplicate")
            hashes.append(source_hash)
        result[history_id] = hashes
    return result


def _implementation_hashes() -> dict[str, str]:
    files = {
        "aligned_live": SRC / "paper_eval/membind_v1/aligned_live.py",
        "graphiti_adapter": SRC / "paper_eval/membind_v1/graphiti_adapter.py",
        "graphiti_factories": SRC / "paper_eval/membind_v1/graphiti_factories.py",
        "semantic_trace_binding": SRC / "paper_eval/membind_v1/semantic_trace_binding.py",
    }
    result: dict[str, str] = {}
    for name, path in files.items():
        digest = sha256_file(path)
        if len(digest) != 64 or digest == "missing":
            raise RunnerError("MemBind implementation fingerprint unavailable")
        result[name] = digest
    return result


def _load_default_env() -> Mapping[str, str]:
    from graphiti_native import load_env_file

    return load_env_file(LEGACY / ".env")


def _load_default_workload() -> dict[str, dict[str, object]]:
    from dataset import build_episodes, load_json_records

    records = {
        str(item.get("question_id")): item
        for item in load_json_records(DATASET)
        if isinstance(item, Mapping)
    }
    result: dict[str, dict[str, object]] = {}
    for history_id in HISTORIES:
        record = records.get(history_id)
        if record is None:
            raise RunnerError("development history is missing from the dataset")
        result[history_id] = {
            "record": record,
            "episodes": tuple(build_episodes(record)),
        }
    _source_hashes(result)
    return result


def _load_historical_references() -> dict[str, object]:
    roots = {
        "baseline": next(
            (path for path in (PROJECT / "artifacts/paper_eval/baseline_suite/runs").glob(
                "*/THREE_BASELINE_RESULTS.json"
            )),
            None,
        ),
        "report": next(
            (path for path in (PROJECT / "artifacts/paper_eval/development_report/runs").glob(
                "*/REPORT.json"
            )),
            None,
        ),
        "overlay": next(
            (path for path in (PROJECT / "artifacts/paper_eval/graph_quality_overlay/runs").glob(
                "*/GRAPH_QUALITY_RESULTS.json"
            )),
            None,
        ),
        "decision": next(
            (path for path in (PROJECT / "artifacts/paper_eval/methodology_finalization/runs").glob(
                "*/METHODOLOGY_DECISION.json"
            )),
            None,
        ),
        "envelope": next(
            (path for path in (PROJECT / "artifacts/paper_eval/methodology_finalization/runs").glob(
                "*/FINAL_METHODOLOGY_ENVELOPE.json"
            )),
            None,
        ),
    }
    if any(path is None for path in roots.values()):
        raise RunnerError("sealed historical reference artifact is missing")
    return bind_sealed_historical_references(
        baseline_suite=_read_json(roots["baseline"]),  # type: ignore[arg-type]
        development_report=_read_json(roots["report"]),  # type: ignore[arg-type]
        graph_quality_overlay=_read_json(roots["overlay"]),  # type: ignore[arg-type]
        methodology_decision=_read_json(roots["decision"]),  # type: ignore[arg-type]
        final_methodology_envelope=_read_json(roots["envelope"]),  # type: ignore[arg-type]
        methodology_document=(PROJECT.parent / "主methodology设计.md").read_text(
            encoding="utf-8"
        ),
    )


@dataclass(frozen=True)
class Hooks:
    """Injectable boundaries used by offline tests and the live command."""

    load_env: Callable[[], Mapping[str, str]]
    load_workload: Callable[[], Mapping[str, Mapping[str, object]]]
    project_runtime_identity: Callable[[Mapping[str, str]], Mapping[str, object]]
    implementation_hashes: Callable[[], Mapping[str, str]]
    bind_historical: Callable[[], Mapping[str, object]]
    execute_smoke: Callable[..., Any]
    verify_smoke: Callable[..., Mapping[str, object]]
    build_quality_runtime: Callable[[Mapping[str, str]], object]
    close_quality_runtime: Callable[[object], Any]
    execute_block: Callable[..., Any]
    measure_quality: Callable[..., Any]
    derive_block: Callable[..., Mapping[str, object]]
    verify_block: Callable[..., Mapping[str, object]]
    reduce_blocks: Callable[..., Sequence[Mapping[str, object]]]
    build_table: Callable[..., Mapping[str, object]]
    render_table: Callable[[Mapping[str, object]], str]


async def _default_execute_block(**kwargs: object) -> Mapping[str, object]:
    block = kwargs["block"]
    workload = kwargs["workload_entry"]
    if not isinstance(block, Mapping) or not isinstance(workload, Mapping):
        raise RunnerError("live block input invalid")
    episodes = workload.get("episodes")
    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise RunnerError("live block episode input invalid")
    return await execute_aligned_live_block(
        verified_plan=kwargs["plan"],
        block_index=int(block["block_index"]),
        episodes=episodes,
        env=kwargs["env"],
        block_root=kwargs["block_root"],
        execution_identity_sha256=kwargs["execution_identity_sha256"],
        membind_artifact_identity=kwargs.get("artifact_identity"),
    )


def _smoke_run_id(aligned_run_id: str) -> str:
    """Derive a deterministic, path-safe ID without exposing workload data."""

    return f"smoke-{hashlib.sha256(aligned_run_id.encode('utf-8')).hexdigest()[:24]}"


async def _default_execute_smoke(**kwargs: object) -> Mapping[str, object]:
    plan = kwargs.get("plan")
    workload_entry = kwargs.get("workload_entry")
    episodes = workload_entry.get("episodes") if isinstance(workload_entry, Mapping) else None
    if not isinstance(plan, Mapping) or isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise RunnerError("smoke workload input invalid")
    result = await run_membind_v1_smoke(
        Path(kwargs["smoke_root"]),
        smoke_run_id=str(kwargs["smoke_run_id"]),
        formal_verified_plan=plan,
        history_id=str(kwargs["history_id"]),
        episodes=episodes,
        sample_count=3,
        env=kwargs["env"],
        execution_identity_sha256=str(kwargs["execution_identity_sha256"]),
        membind_artifact_identity=kwargs["artifact_identity"],
    )
    return result


def _default_verify_smoke(**kwargs: object) -> Mapping[str, object]:
    plan = kwargs.get("plan")
    artifact_identity = kwargs.get("artifact_identity")
    if not isinstance(plan, Mapping) or not isinstance(artifact_identity, NodeArtifactIdentity):
        raise RunnerError("smoke verification input invalid")
    inspected = inspect_membind_v1_smoke(Path(kwargs["smoke_root"]))
    manifest = inspected.get("manifest")
    smoke_plan = inspected.get("smoke_plan")
    if not isinstance(manifest, Mapping) or not isinstance(smoke_plan, Mapping):
        raise RunnerError("smoke verification artifact invalid")
    formal_sources = plan.get("history_source_sha256s")
    smoke_sources = smoke_plan.get("history_source_sha256s")
    if not isinstance(formal_sources, Mapping) or not isinstance(smoke_sources, Mapping):
        raise RunnerError("smoke verification source inventory invalid")
    for history_id in ALIGNED_DEVELOPMENT_HISTORIES:
        expected = formal_sources.get(history_id)
        observed = smoke_sources.get(history_id)
        if not isinstance(expected, list) or observed != expected[:3]:
            raise RunnerError("smoke verification source prefix drift")
    if (
        manifest.get("formal_plan_payload_sha256") != plan.get("payload_sha256")
        or manifest.get("execution_identity_sha256")
        != kwargs.get("execution_identity_sha256")
        or manifest.get("shared_execution_envelope_sha256")
        != plan.get("shared_execution_envelope_sha256")
        or manifest.get("history_id") != ALIGNED_DEVELOPMENT_HISTORIES[0]
        or manifest.get("method") != "MemBind-v1 node-only"
        or manifest.get("source_count") != 3
        or manifest.get("global_llm_admission_k") != 2
        or payload_sha256(asdict(artifact_identity))
        != manifest.get("membind_artifact_identity_sha256")
    ):
        raise RunnerError("smoke verification identity drift")
    result = inspected.get("result")
    if not isinstance(result, Mapping):
        raise RunnerError("completed smoke result missing")
    return result


async def _default_measure_quality(**kwargs: object) -> Mapping[str, object]:
    block = kwargs["block"]
    workload = kwargs["workload_entry"]
    runtime = kwargs["quality_runtime"]
    if not isinstance(block, Mapping) or not isinstance(workload, Mapping):
        raise RunnerError("quality block input invalid")
    record = workload.get("record")
    episodes = workload.get("episodes")
    graph = getattr(runtime, "graphiti", None)
    if not isinstance(record, Mapping) or isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise RunnerError("quality workload input invalid")
    hooks = build_aligned_quality_hooks(graph=graph, record=record, episodes=episodes)
    from paper_eval.membind_v1.aligned_quality_production import build_session_retrieval_case

    result = await observe_aligned_quality_live(
        kwargs["block_root"],
        verified_plan=kwargs["plan"],
        block_index=int(block["block_index"]),
        retrieval_cases=(build_session_retrieval_case(record=record),),
        hooks=hooks,
    )
    return result


def _quality_projection_seal(quality: Mapping[str, object]) -> str:
    stored = quality.get("quality_and_correctness_sha256")
    if not isinstance(stored, str) or len(stored) != 64:
        raise RunnerError("aligned quality projection seal missing")
    try:
        int(stored, 16)
    except ValueError:
        raise RunnerError("aligned quality projection seal invalid") from None
    body = {
        key: item
        for key, item in quality.items()
        if key != "quality_and_correctness_sha256"
    }
    if stored != payload_sha256(body):
        raise RunnerError("aligned quality projection seal invalid")
    return stored


def _default_verify_block(**kwargs: object) -> Mapping[str, object]:
    root = kwargs["block_root"]
    if not isinstance(root, Path):
        raise RunnerError("block root invalid")
    output = _read_json(root / "block_output.json")
    output_seal = output.get("block_output_sha256")
    output_body = {
        key: item for key, item in output.items() if key != "block_output_sha256"
    }
    if not isinstance(output_seal, str) or output_seal != payload_sha256(output_body):
        raise RunnerError("completed block output seal drift")
    if output.get("status") != "PASS" or output.get("block_index") != kwargs["block"]["block_index"]:
        raise RunnerError("completed block output invalid")
    quality = _read_json(root / "quality_and_correctness.json")
    quality_seal = _quality_projection_seal(quality)
    if output.get("quality_and_correctness_sha256") != quality_seal:
        raise RunnerError("completed block output quality seal drift")
    derived = derive_aligned_block_output(
        root,
        verified_plan=kwargs["plan"],
        block_index=int(kwargs["block"]["block_index"]),
        quality_and_correctness=quality,
    )
    if output.get("derived") != derived:
        raise RunnerError("completed block output seal drift")
    return derived


def _default_build_table(**kwargs: object) -> Mapping[str, object]:
    return build_development_main_table(
        main_table_run_id=str(kwargs["main_table_run_id"]),
        historical_references=kwargs["historical_references"],
        aligned_rows=kwargs["aligned_rows"],
    )


def _default_hooks() -> Hooks:
    return Hooks(
        load_env=_load_default_env,
        load_workload=_load_default_workload,
        project_runtime_identity=project_membind_v1_runtime_identity,
        implementation_hashes=_implementation_hashes,
        bind_historical=_load_historical_references,
        execute_smoke=_default_execute_smoke,
        verify_smoke=_default_verify_smoke,
        build_quality_runtime=lambda env: build_read_only_aligned_quality_runtime(env=env),
        close_quality_runtime=close_read_only_aligned_quality_runtime,
        execute_block=_default_execute_block,
        measure_quality=_default_measure_quality,
        derive_block=lambda **kwargs: derive_aligned_block_output(
            kwargs["block_root"],
            verified_plan=kwargs["plan"],
            block_index=int(kwargs["block"]["block_index"]),
            quality_and_correctness=kwargs["quality_and_correctness"],
        ),
        verify_block=_default_verify_block,
        reduce_blocks=lambda **kwargs: reduce_aligned_blocks(
            verified_plan=kwargs["plan"],
            public_rows=kwargs["public_rows"],
            freshness_records=kwargs["freshness_records"],
        ),
        build_table=_default_build_table,
        render_table=render_development_main_table_markdown,
    )


DEFAULT_HOOKS = _default_hooks()


def _progress(
    *,
    aligned_run_id: str,
    plan: Mapping[str, object],
    completed: Sequence[int],
    status: str,
    failed_block_index: int | None = None,
    error_class: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "membind.paper-eval-v3.membind-v1-aligned-progress.v1",
        "aligned_run_id": aligned_run_id,
        "plan_payload_sha256": plan["payload_sha256"],
        "status": status,
        "completed_block_indices": list(completed),
        "expected_block_count": 12,
        "failed_block_index": failed_block_index,
        "error_class": error_class,
        "result_mergeable": status == "PASS",
    }


def _validated_smoke_gate(
    result: Mapping[str, object],
    *,
    smoke_root: Path,
    smoke_run_id: str,
    plan: Mapping[str, object],
    execution_identity_sha256: str,
    artifact_identity: NodeArtifactIdentity,
) -> dict[str, object]:
    if result.get("status") != "PASS":
        raise RunnerError("MemBind-v1 smoke did not pass")
    result_sha = result.get("payload_sha256")
    if not isinstance(result_sha, str) or len(result_sha) != 64:
        raise RunnerError("MemBind-v1 smoke result seal missing")
    try:
        int(result_sha, 16)
    except ValueError:
        raise RunnerError("MemBind-v1 smoke result seal invalid") from None
    result_body = {key: value for key, value in result.items() if key != "payload_sha256"}
    if result_sha != payload_sha256(result_body):
        raise RunnerError("MemBind-v1 smoke result seal invalid")
    expected_artifact_identity_sha256 = payload_sha256(asdict(artifact_identity))
    formal_sources = plan.get("history_source_sha256s")
    if not isinstance(formal_sources, Mapping):
        raise RunnerError("MemBind-v1 smoke source inventory invalid")
    smoke_sources = {
        history_id: list(formal_sources[history_id])[:3]
        for history_id in ALIGNED_DEVELOPMENT_HISTORIES
    }
    expected_smoke_source_manifest_sha256 = payload_sha256(smoke_sources)
    if (
        result.get("formal_plan_payload_sha256") != plan.get("payload_sha256")
        or result.get("execution_identity_sha256") != execution_identity_sha256
        or result.get("membind_artifact_identity_sha256")
        != expected_artifact_identity_sha256
        or result.get("history_id") != ALIGNED_DEVELOPMENT_HISTORIES[0]
        or result.get("method") != "MemBind-v1 node-only"
        or result.get("source_count") != 3
        or result.get("global_llm_admission_k") != 2
        or result.get("shared_execution_envelope_sha256")
        != plan.get("shared_execution_envelope_sha256")
        or result.get("source_manifest_sha256")
        != expected_smoke_source_manifest_sha256
    ):
        raise RunnerError("MemBind-v1 smoke identity binding drift")
    manifest_sha256 = result.get("manifest_sha256")
    if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64:
        raise RunnerError("MemBind-v1 smoke manifest seal missing")
    live_result = result.get("live_result")
    if not isinstance(live_result, Mapping) or {
        live_result.get("status"),
        live_result.get("method"),
        live_result.get("source_count"),
        live_result.get("execution_identity_sha256"),
        live_result.get("shared_execution_envelope_sha256"),
    } != {
        "PASS",
        "MemBind-v1 node-only",
        3,
        execution_identity_sha256,
        plan.get("shared_execution_envelope_sha256"),
    }:
        raise RunnerError("MemBind-v1 smoke live-result binding drift")
    body = {
        "schema_version": "membind.paper-eval-v3.membind-v1-smoke-gate.v1",
        "status": "PASS",
        "smoke_run_id": smoke_run_id,
        "smoke_attempt_root": str(smoke_root),
        "formal_plan_payload_sha256": plan["payload_sha256"],
        "execution_identity_sha256": execution_identity_sha256,
        "membind_artifact_identity_sha256": expected_artifact_identity_sha256,
        "shared_execution_envelope_sha256": plan["shared_execution_envelope_sha256"],
        "smoke_manifest_sha256": manifest_sha256,
        "smoke_result_payload_sha256": result_sha,
        "sample_count": 3,
        "history_id": ALIGNED_DEVELOPMENT_HISTORIES[0],
        "formal_blocks_authorized": True,
    }
    return {**body, "payload_sha256": payload_sha256(body)}


def _run_or_verify_smoke(
    *,
    aligned_run_id: str,
    plan: Mapping[str, object],
    workload: Mapping[str, Mapping[str, object]],
    env: Mapping[str, str],
    artifact_identity: NodeArtifactIdentity,
    execution_identity_sha256: str,
    membind_root: Path,
    table_root: Path,
    hooks: Hooks,
) -> dict[str, object]:
    history_id = ALIGNED_DEVELOPMENT_HISTORIES[0]
    workload_entry = workload.get(history_id)
    if not isinstance(workload_entry, Mapping):
        raise RunnerError("smoke workload entry missing")
    smoke_root = membind_root / "smoke-attempt"
    smoke_run_id = _smoke_run_id(aligned_run_id)
    _safe_write(
        table_root / "progress.json",
        _progress(
            aligned_run_id=aligned_run_id,
            plan=plan,
            completed=(),
            status="SMOKE_RUNNING",
        ),
    )
    try:
        if smoke_root.exists():
            observed = hooks.verify_smoke(
                smoke_root=smoke_root,
                smoke_run_id=smoke_run_id,
                plan=plan,
                workload_entry=workload_entry,
                history_id=history_id,
                env=env,
                execution_identity_sha256=execution_identity_sha256,
                artifact_identity=artifact_identity,
            )
        else:
            observed = asyncio.run(
                hooks.execute_smoke(
                    smoke_root=smoke_root,
                    smoke_run_id=smoke_run_id,
                    plan=plan,
                    workload_entry=workload_entry,
                    history_id=history_id,
                    env=env,
                    execution_identity_sha256=execution_identity_sha256,
                    artifact_identity=artifact_identity,
                )
            )
        if not isinstance(observed, Mapping):
            raise RunnerError("MemBind-v1 smoke result invalid")
        gate = _validated_smoke_gate(
            observed,
            smoke_root=smoke_root,
            smoke_run_id=smoke_run_id,
            plan=plan,
            execution_identity_sha256=execution_identity_sha256,
            artifact_identity=artifact_identity,
        )
        gate_path = table_root / "SMOKE_GATE.json"
        if gate_path.exists() and _read_json(gate_path) != gate:
            raise RunnerError("existing smoke gate drift")
        if not gate_path.exists():
            _safe_write(gate_path, gate)
        return gate
    except BaseException as error:
        if isinstance(error, asyncio.CancelledError):
            raise
        _safe_write(
            table_root / "progress.json",
            _progress(
                aligned_run_id=aligned_run_id,
                plan=plan,
                completed=(),
                status="SMOKE_FAILED_STOPPED",
                failed_block_index=None,
                error_class=_qualified_error(error),
            ),
        )
        raise


def _persist_block_output(
    root: Path,
    *,
    block: Mapping[str, object],
    quality_live: Mapping[str, object],
    quality: Mapping[str, object],
    derived: Mapping[str, object],
) -> None:
    quality_seal = _quality_projection_seal(quality)
    output_body = {
        "schema_version": "membind.paper-eval-v3.membind-v1-aligned-block-output.v1",
        "status": "PASS",
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "quality_and_correctness_sha256": quality_seal,
        "derived": dict(derived),
    }
    _safe_write(root / "quality_live.json", quality_live)
    _safe_write(root / "quality_and_correctness.json", quality)
    _safe_write(root / "derived_block.json", derived)
    _safe_write(
        root / "block_output.json",
        {
            **output_body,
            "block_output_sha256": payload_sha256(output_body),
        },
    )


def _check_ids(aligned_run_id: str, main_table_run_id: str) -> None:
    if not isinstance(aligned_run_id, str) or _ALIGNED_RUN_ID.fullmatch(aligned_run_id) is None:
        raise RunnerError("aligned run id invalid")
    if not isinstance(main_table_run_id, str) or _MAIN_TABLE_RUN_ID.fullmatch(main_table_run_id) is None:
        raise RunnerError("main table run id invalid")


async def _run_blocks(
    *,
    aligned_run_id: str,
    main_table_run_id: str,
    plan: Mapping[str, object],
    workload: Mapping[str, Mapping[str, object]],
    env: Mapping[str, str],
    artifact_identity: NodeArtifactIdentity,
    execution_identity_sha256: str,
    historical_references: Mapping[str, object],
    table_root: Path,
    quality_runtime: object,
    hooks: Hooks,
) -> dict[str, object]:
    completed: list[int] = []
    public_rows: list[Mapping[str, object]] = []
    freshness_records: list[Mapping[str, object]] = []
    _safe_write(table_root / "progress.json", _progress(aligned_run_id=aligned_run_id, plan=plan, completed=completed, status="RUNNING"))
    blocks = plan.get("blocks")
    if not isinstance(blocks, list) or len(blocks) != 12:
        raise RunnerError("aligned plan block inventory invalid")
    try:
        for block_value in blocks:
            if not isinstance(block_value, Mapping):
                raise RunnerError("aligned block invalid")
            block = dict(block_value)
            index = int(block["block_index"])
            history_id = str(block["history_id"])
            entry = workload.get(history_id)
            if not isinstance(entry, Mapping):
                raise RunnerError("aligned workload entry missing")
            block_root = table_root / "blocks" / f"{index:02d}-{str(block['method']).lower().replace('(', '').replace(')', '').replace('*', 'star').replace(' ', '-')}-{history_id}"
            if block_root.exists():
                if not (block_root / "block_output.json").exists():
                    raise RunnerError("existing incomplete block cannot be resumed")
                derived = dict(
                    hooks.verify_block(
                        block_root=block_root,
                        block=block,
                        plan=plan,
                    )
                )
            else:
                execution_result = await hooks.execute_block(
                    plan=plan,
                    block=block,
                    workload_entry=entry,
                    env=env,
                    block_root=block_root,
                    execution_identity_sha256=execution_identity_sha256,
                    artifact_identity=artifact_identity,
                )
                if not isinstance(execution_result, Mapping) or execution_result.get("status") != "PASS":
                    raise RunnerError("aligned block execution did not pass")
                quality_live = await hooks.measure_quality(
                    plan=plan,
                    block=block,
                    workload_entry=entry,
                    quality_runtime=quality_runtime,
                    block_root=block_root,
                )
                if not isinstance(quality_live, Mapping):
                    raise RunnerError("aligned quality observation invalid")
                quality = quality_live.get("quality_and_correctness")
                if not isinstance(quality, Mapping):
                    raise RunnerError("aligned quality projection missing")
                derived = dict(
                    hooks.derive_block(
                        plan=plan,
                        block=block,
                        block_root=block_root,
                        quality_and_correctness=quality,
                    )
                )
                _persist_block_output(
                    block_root,
                    block=block,
                    quality_live=quality_live,
                    quality=quality,
                    derived=derived,
                )
            public_row = derived.get("public_row")
            freshness = derived.get("freshness_record")
            if not isinstance(public_row, Mapping) or not isinstance(freshness, Mapping):
                raise RunnerError("aligned block output projections missing")
            public_rows.append(public_row)
            freshness_records.append(freshness)
            completed.append(index)
            _safe_write(table_root / "progress.json", _progress(aligned_run_id=aligned_run_id, plan=plan, completed=completed, status="RUNNING"))
            if index == 2:
                _safe_write(
                    table_root / "FIRST_HISTORY_GATE.json",
                    {
                        "schema_version": "membind.paper-eval-v3.membind-v1-first-history-gate.v1",
                        "status": "PASS",
                        "aligned_run_id": aligned_run_id,
                        "plan_payload_sha256": plan["payload_sha256"],
                        "completed_block_indices": [0, 1, 2],
                        "source_history": "07741c45",
                    },
                )
        reduced = list(
            hooks.reduce_blocks(
                plan=plan,
                public_rows=public_rows,
                freshness_records=freshness_records,
            )
        )
        table = dict(
            hooks.build_table(
                main_table_run_id=main_table_run_id,
                historical_references=historical_references,
                aligned_rows=reduced,
            )
        )
        _safe_write(table_root / "ALIGNED_MAIN_TABLE.json", table)
        (table_root / "ALIGNED_MAIN_TABLE.md").write_text(
            hooks.render_table(table), encoding="utf-8"
        )
        _safe_write(table_root / "progress.json", _progress(aligned_run_id=aligned_run_id, plan=plan, completed=completed, status="PASS"))
        return {
            "status": "PASS",
            "aligned_run_id": aligned_run_id,
            "main_table_run_id": main_table_run_id,
            "completed_block_count": len(completed),
            "aligned_rows": reduced,
            "table": table,
        }
    except BaseException as error:
        if isinstance(error, asyncio.CancelledError):
            raise
        failed_index = completed[-1] + 1 if completed else 0
        _safe_write(
            table_root / "progress.json",
            _progress(
                aligned_run_id=aligned_run_id,
                plan=plan,
                completed=completed,
                status="FAILED_STOPPED",
                failed_block_index=failed_index,
                error_class=_qualified_error(error),
            ),
        )
        raise


def run_aligned_main_table(
    *,
    aligned_run_id: str,
    main_table_run_id: str,
    membind_runs_root: Path = MEMBIND_RUNS_ROOT,
    aligned_table_runs_root: Path = ALIGNED_TABLE_RUNS_ROOT,
    hooks: Hooks = DEFAULT_HOOKS,
) -> dict[str, object]:
    """Freeze and execute the complete aligned 12-block development lane."""

    _check_ids(aligned_run_id, main_table_run_id)
    table_root = Path(aligned_table_runs_root) / aligned_run_id
    membind_root = Path(membind_runs_root) / aligned_run_id
    table_root.mkdir(parents=True, exist_ok=True)
    membind_root.mkdir(parents=True, exist_ok=True)
    lock_path = table_root / "run.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunnerError("aligned command is already running") from None
        env = dict(hooks.load_env())
        workload = hooks.load_workload()
        sources = _source_hashes(workload)
        public_runtime_identity = dict(hooks.project_runtime_identity(env))
        execution_envelope = payload_sha256(public_runtime_identity)
        implementation_hashes = dict(hooks.implementation_hashes())
        artifact_identity = build_node_artifact_identity(
            runtime_identity=public_runtime_identity,
            implementation_hashes=implementation_hashes,
        )
        execution_identity = payload_sha256(
            {
                "candidate": "MemBind-v1 node-only",
                "runtime_identity": public_runtime_identity,
                "implementation_hashes": implementation_hashes,
            }
        )
        plan = verify_aligned_development_plan(
            build_aligned_development_plan(
                aligned_run_id=aligned_run_id,
                history_source_sha256s=sources,
                interarrival_ns=FROZEN_INTERARRIVAL_NS,
                shared_execution_envelope_sha256=execution_envelope,
            )
        )
        plan_path = table_root / "ALIGNED_PLAN.json"
        if plan_path.exists() and _read_json(plan_path) != plan:
            raise RunnerError("existing aligned plan drift")
        if not plan_path.exists():
            _safe_write(plan_path, plan)
        historical = dict(hooks.bind_historical())
        historical_path = table_root / "HISTORICAL_REFERENCE.json"
        if historical_path.exists() and _read_json(historical_path) != historical:
            raise RunnerError("existing historical reference drift")
        if not historical_path.exists():
            _safe_write(historical_path, historical)
        run_manifest = {
            "schema_version": "membind.paper-eval-v3.membind-v1-aligned-run-manifest.v1",
            "status": "ACTIVE",
            "aligned_run_id": aligned_run_id,
            "main_table_run_id": main_table_run_id,
            "plan_payload_sha256": plan["payload_sha256"],
            "execution_identity_sha256": execution_identity,
            "shared_execution_envelope_sha256": execution_envelope,
            "global_llm_admission_k": 2,
            "implementation_hashes": implementation_hashes,
            "artifact_identity": asdict(artifact_identity),
            "historical_reference_sha256": historical["payload_sha256"],
            "interarrival_ns": FROZEN_INTERARRIVAL_NS,
            "load_reference": {
                "native_service_reference_ns": 50_173_429_214,
                "normalized_offered_load": 1.2,
                "derivation": "interarrival = native_service_reference / rho",
            },
        }
        run_manifest_path = membind_root / "RUN_MANIFEST.json"
        if run_manifest_path.exists():
            if _read_json(run_manifest_path) != run_manifest:
                raise RunnerError("existing run manifest drift")
        else:
            _safe_write(run_manifest_path, run_manifest)
        _run_or_verify_smoke(
            aligned_run_id=aligned_run_id,
            plan=plan,
            workload=workload,
            env=env,
            artifact_identity=artifact_identity,
            execution_identity_sha256=execution_identity,
            membind_root=membind_root,
            table_root=table_root,
            hooks=hooks,
        )
        quality_runtime = hooks.build_quality_runtime(env)
        try:
            return asyncio.run(
                _run_blocks(
                    aligned_run_id=aligned_run_id,
                    main_table_run_id=main_table_run_id,
                    plan=plan,
                    workload=workload,
                    env=env,
                    artifact_identity=artifact_identity,
                    execution_identity_sha256=execution_identity,
                    historical_references=historical,
                    table_root=table_root,
                    quality_runtime=quality_runtime,
                    hooks=hooks,
                )
            )
        finally:
            close_result = hooks.close_quality_runtime(quality_runtime)
            if inspect.isawaitable(close_result):
                asyncio.run(close_result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aligned-run-id", required=True)
    parser.add_argument("--main-table-run-id", required=True)
    args = parser.parse_args()
    result = run_aligned_main_table(
        aligned_run_id=args.aligned_run_id,
        main_table_run_id=args.main_table_run_id,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "table"}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
