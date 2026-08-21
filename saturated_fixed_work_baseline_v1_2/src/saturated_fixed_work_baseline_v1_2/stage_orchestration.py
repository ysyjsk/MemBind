"""Fail-closed L1, L2, and L3 orchestration over tested live primitives."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .contracts import EpisodeInput, ResumeIdentity
from .dataset import EXPECTED_SOURCE_TOKENS, load_episode_inputs
from .formal_run_seal import write_formal_run_seal
from .live import FormalBlock, build_formal_plan, derive_cache_salt, derive_namespace
from .live_block import LiveBlockDependencies, execute_live_block
from .preflight_seal import verify_preflight_seal
from .qualification import serial_serial_12_diagnostic
from .qualification_seal import verify_qualification_seal, write_qualification_seal
from .schedules import Method


class StageOrchestrationError(ValueError):
    """A live stage prerequisite, plan, block, or durable seal is invalid."""


BlockExecutor = Callable[..., Awaitable[Mapping[str, Any]]]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise StageOrchestrationError("STAGE_IDENTITY_ARTIFACT_UNREADABLE") from None


def _read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise StageOrchestrationError("STAGE_ARTIFACT_INVALID")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise StageOrchestrationError("STAGE_ARTIFACT_INVALID") from None
    if not isinstance(value, dict):
        raise StageOrchestrationError("STAGE_ARTIFACT_INVALID")
    return value


def _write_new_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    selected = dict(value)
    selected["payload_sha256"] = _hash(selected)
    payload = json.dumps(
        selected, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise StageOrchestrationError("STAGE_ARTIFACT_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return selected


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _run_id(root: Path) -> str:
    value = _read_object(root / "protocol_manifest.json")
    run_id = value.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise StageOrchestrationError("RUN_ID_INVALID")
    return run_id


def _resume_identity(root: Path, block: FormalBlock) -> ResumeIdentity:
    audit = _read_object(root / "audit_manifest.json")
    dataset = audit.get("dataset")
    provider = _read_object(root / "provider_envelope.json")
    if not isinstance(dataset, Mapping):
        raise StageOrchestrationError("STAGE_DATASET_IDENTITY_INVALID")
    project = audit.get("head")
    data = dataset.get("source_file_sha256")
    provider_hash = provider.get("payload_sha256")
    if not all(isinstance(value, str) and value for value in (project, data, provider_hash)):
        raise StageOrchestrationError("STAGE_RESUME_IDENTITY_INVALID")
    try:
        resource = (root / "RESOURCE_ENVELOPE_ID").read_text(
            encoding="ascii"
        ).strip()
    except (OSError, UnicodeError):
        raise StageOrchestrationError("STAGE_RESUME_IDENTITY_INVALID") from None
    return ResumeIdentity(
        project_sha256=hashlib.sha256(project.encode("ascii")).hexdigest(),
        data_sha256=str(data),
        provider_sha256=str(provider_hash),
        resource_sha256=resource,
        config_sha256=_file_hash(root / "config_hashes.json"),
        cache_sha256=hashlib.sha256(block.cache_salt.encode("ascii")).hexdigest(),
        namespace=block.namespace,
    )


def _stage_block(
    *,
    base_run_id: str,
    stage: str,
    block_id: str,
    ordinal: int,
    method: Method,
    history_id: str = "07741c45",
) -> FormalBlock:
    stage_run_id = f"{base_run_id}-{stage}"
    return FormalBlock(
        ordinal=ordinal,
        block_id=block_id,
        run_id=stage_run_id,
        history_id=history_id,
        method=method,
        attempt_ordinal=1,
        namespace=derive_namespace(
            stage_run_id, method, history_id, attempt_ordinal=1
        ),
        cache_salt=derive_cache_salt(stage_run_id, block_id, attempt_ordinal=1),
    )


_ATTEMPT_DIRECTORY = re.compile(r"^attempt-(?P<ordinal>[0-9]{3,})$")


def _formal_resume_block(root: Path, block: FormalBlock) -> FormalBlock | None:
    block_root = root / "blocks" / block.block_id
    if not block_root.exists():
        return block
    if block_root.is_symlink() or not block_root.is_dir():
        raise StageOrchestrationError("FORMAL_BLOCK_DIRECTORY_INVALID")
    attempts = sorted(
        path
        for path in block_root.iterdir()
        if path.is_dir() and _ATTEMPT_DIRECTORY.fullmatch(path.name)
    )
    if not attempts:
        return block
    ordinals: list[int] = []
    for attempt in attempts:
        match = _ATTEMPT_DIRECTORY.fullmatch(attempt.name)
        assert match is not None
        ordinal = int(match.group("ordinal"))
        ordinals.append(ordinal)
        terminal = [
            name
            for name in ("seal.json", "failure.json", "timeout_diagnosis.json")
            if (attempt / name).is_file()
        ]
        if len(terminal) != 1:
            raise StageOrchestrationError("FORMAL_ATTEMPT_NONTERMINAL")
        if terminal[0] == "seal.json":
            return None
    next_ordinal = max(ordinals) + 1
    return FormalBlock(
        ordinal=block.ordinal,
        block_id=block.block_id,
        run_id=block.run_id,
        history_id=block.history_id,
        method=block.method,
        attempt_ordinal=next_ordinal,
        namespace=derive_namespace(
            block.run_id,
            block.method,
            block.history_id,
            attempt_ordinal=next_ordinal,
        ),
        cache_salt=derive_cache_salt(
            block.run_id, block.block_id, attempt_ordinal=next_ordinal
        ),
    )


def _default_source_tokens(root: Path, episodes: Sequence[EpisodeInput]) -> int:
    selected = tuple(episodes)
    if not selected:
        raise StageOrchestrationError("STAGE_EPISODES_INVALID")
    history = selected[0].history_id
    if len(selected) == len(load_episode_inputs(root, history, selected[0].namespace)):
        return EXPECTED_SOURCE_TOKENS[history]
    # Qualification rows never enter the main table. Exact prefix token counts
    # are still required from the production composition rather than guessed.
    raise StageOrchestrationError("PREFIX_SOURCE_TOKEN_COUNTER_REQUIRED")


async def _execute(
    *,
    repository_root: Path,
    stage_root: Path,
    identity_root: Path,
    block: FormalBlock,
    dependencies: LiveBlockDependencies | object,
    prepare_block: Callable[[FormalBlock], Any],
    block_executor: BlockExecutor,
    episode_loader: Callable[[Path, str, str], Sequence[EpisodeInput]],
    source_token_counter: Callable[[Path, Sequence[EpisodeInput]], int],
    identity_builder: Callable[[Path, FormalBlock], ResumeIdentity],
    episode_limit: int | None = None,
) -> tuple[dict[str, Any], tuple[EpisodeInput, ...]]:
    prepared = await _await(prepare_block(block))
    if prepared is not True:
        raise StageOrchestrationError("BLOCK_PREPARATION_FAILED")
    episodes = tuple(
        episode_loader(repository_root, block.history_id, block.namespace)
    )
    if episode_limit is not None:
        episodes = episodes[:episode_limit]
    if not episodes or [row.source_sequence for row in episodes] != list(
        range(len(episodes))
    ):
        raise StageOrchestrationError("STAGE_EPISODES_INVALID")
    source_tokens = source_token_counter(repository_root, episodes)
    if isinstance(source_tokens, bool) or not isinstance(source_tokens, int) or source_tokens <= 0:
        raise StageOrchestrationError("STAGE_SOURCE_TOKENS_INVALID")
    result = await block_executor(
        repository_root=repository_root,
        run_root=stage_root,
        block=block,
        identity=identity_builder(identity_root, block),
        episodes=episodes,
        dependencies=dependencies,
        source_tokens=source_tokens,
    )
    if not isinstance(result, Mapping) or result.get("valid") is not True:
        raise StageOrchestrationError("STAGE_BLOCK_INVALID")
    return dict(result), episodes


def _canonical_graph(
    stage_root: Path, block: FormalBlock, result: Mapping[str, Any]
) -> dict[str, Any]:
    embedded = result.get("canonical_graph")
    if isinstance(embedded, Mapping):
        return dict(embedded)
    return _read_object(
        stage_root / "blocks" / block.block_id / "attempt-001/canonical_graph.json"
    )


def _schedule_valid(result: Mapping[str, Any], method: Method, count: int) -> bool:
    return (
        result.get("created_sequences") == list(range(count))
        and result.get("feeder_workload_await_count")
        == (count if method is Method.B0_NATIVE_SERIAL else 0)
        and result.get("application_gate_count") == 0
        and result.get("artificial_sleep_count") == 0
        and result.get("configured_max_inflight") is None
    )


async def execute_qualification_stage(
    *,
    repository_root: Path,
    run_root: Path,
    dependencies: LiveBlockDependencies | object,
    instrumentation_aa: Mapping[str, Any],
    prepare_block: Callable[[FormalBlock], Any],
    qa_read_only_probe: Callable[[Sequence[Mapping[str, Any]]], Any],
    preflight_verifier: Callable[[Path], Mapping[str, Any]] = verify_preflight_seal,
    block_executor: BlockExecutor = execute_live_block,
    episode_loader: Callable[[Path, str, str], Sequence[EpisodeInput]] = load_episode_inputs,
    source_token_counter: Callable[[Path, Sequence[EpisodeInput]], int] = _default_source_tokens,
    identity_builder: Callable[[Path, FormalBlock], ResumeIdentity] = _resume_identity,
    seal_writer: Callable[[Path, Mapping[str, Any]], Mapping[str, Any]] = write_qualification_seal,
) -> dict[str, Any]:
    root = run_root.resolve()
    preflight = preflight_verifier(root)
    if preflight.get("verified") is not True or preflight.get("preflight_passed") is not True:
        raise StageOrchestrationError("PREFLIGHT_NOT_VERIFIED")
    if instrumentation_aa.get("qualified") is not True:
        raise StageOrchestrationError("INSTRUMENTATION_AA_NOT_QUALIFIED")
    stage_root = root / "qualification/l1-attempt-001"
    definitions = (
        ("qualification-b0-a", Method.B0_NATIVE_SERIAL),
        ("qualification-b0-b", Method.B0_NATIVE_SERIAL),
        ("qualification-b1", Method.B1_NAIVE_WHOLE_UPDATE_ASYNC),
    )
    outputs: list[dict[str, Any]] = []
    graphs: list[dict[str, Any]] = []
    for ordinal, (block_id, method) in enumerate(definitions, start=1):
        block = _stage_block(
            base_run_id=_run_id(root),
            stage="qualification",
            block_id=block_id,
            ordinal=ordinal,
            method=method,
        )
        result, _episodes = await _execute(
            repository_root=repository_root,
            stage_root=stage_root,
            identity_root=root,
            block=block,
            dependencies=dependencies,
            prepare_block=prepare_block,
            block_executor=block_executor,
            episode_loader=episode_loader,
            source_token_counter=source_token_counter,
            identity_builder=identity_builder,
            episode_limit=12,
        )
        if not _schedule_valid(result, method, 12):
            raise StageOrchestrationError("QUALIFICATION_SCHEDULE_INVALID")
        outputs.append({"block": asdict(block), "metrics": result})
        graphs.append(_canonical_graph(stage_root, block, result))
    qa_passed = await _await(qa_read_only_probe(outputs))
    if qa_passed is not True:
        raise StageOrchestrationError("QUALIFICATION_QA_READ_ONLY_FAILED")
    serial = serial_serial_12_diagnostic(graphs[0], graphs[1])
    diagnostic = _write_new_json(
        stage_root / "qualification_diagnostics.json",
        {
            "schema_version": "membind.saturated-fixed-work.qualification-diagnostics.v1",
            "instrumentation_aa": dict(instrumentation_aa),
            "serial_serial_12": serial,
            "b1_canonical_graph_hash": _hash(graphs[2]),
            "blocks": outputs,
        },
    )
    evidence = {
        "preflight_passed": True,
        "instrumentation_aa_qualified": True,
        "b0_a_valid": True,
        "b0_b_valid": True,
        "b1_valid": True,
        "b0_schedule_contract": True,
        "b1_schedule_contract": True,
        "qa_read_only_passed": True,
        "canonical_diffs_emitted": True,
        "serial_serial_12_scope": "12_EPISODE_QUALIFICATION_ONLY",
        "qualification_root": "qualification/l1-attempt-001",
        "qualification_diagnostics_payload_sha256": diagnostic["payload_sha256"],
    }
    sealed = dict(seal_writer(root, evidence))
    if sealed.get("qualification_passed") is not True:
        raise StageOrchestrationError("QUALIFICATION_SEAL_FAILED")
    return sealed


def _verify_rehearsal(root: Path) -> dict[str, Any]:
    path = root / "rehearsal/rehearsal_seal.json"
    value = _read_object(path)
    candidate = dict(value)
    observed = candidate.pop("payload_sha256", None)
    if (
        observed != _hash(candidate)
        or candidate.get("rehearsal_passed") is not True
        or candidate.get("block_count") != 2
        or candidate.get("qa_read_only_passed") is not True
    ):
        raise StageOrchestrationError("REHEARSAL_SEAL_INVALID")
    return value


async def execute_rehearsal_stage(
    *,
    repository_root: Path,
    run_root: Path,
    dependencies: LiveBlockDependencies | object,
    prepare_block: Callable[[FormalBlock], Any],
    qa_read_only_probe: Callable[[Sequence[Mapping[str, Any]]], Any],
    qualification_verifier: Callable[[Path], Mapping[str, Any]] = verify_qualification_seal,
    block_executor: BlockExecutor = execute_live_block,
    episode_loader: Callable[[Path, str, str], Sequence[EpisodeInput]] = load_episode_inputs,
    source_token_counter: Callable[[Path, Sequence[EpisodeInput]], int] = _default_source_tokens,
    identity_builder: Callable[[Path, FormalBlock], ResumeIdentity] = _resume_identity,
) -> dict[str, Any]:
    root = run_root.resolve()
    qualified = qualification_verifier(root)
    if qualified.get("verified") is not True or qualified.get("qualification_passed") is not True:
        raise StageOrchestrationError("QUALIFICATION_NOT_VERIFIED")
    stage_root = root / "rehearsal"
    outputs: list[dict[str, Any]] = []
    for ordinal, method in enumerate(Method, start=1):
        block = _stage_block(
            base_run_id=_run_id(root),
            stage="rehearsal",
            block_id=f"rehearsal-{ordinal:03d}-07741c45-{method.value}",
            ordinal=ordinal,
            method=method,
        )
        result, _episodes = await _execute(
            repository_root=repository_root,
            stage_root=stage_root,
            identity_root=root,
            block=block,
            dependencies=dependencies,
            prepare_block=prepare_block,
            block_executor=block_executor,
            episode_loader=episode_loader,
            source_token_counter=source_token_counter,
            identity_builder=identity_builder,
        )
        outputs.append({"block": asdict(block), "metrics": result})
    if await _await(qa_read_only_probe(outputs)) is not True:
        raise StageOrchestrationError("REHEARSAL_QA_READ_ONLY_FAILED")
    return _write_new_json(
        root / "rehearsal/rehearsal_seal.json",
        {
            "schema_version": "membind.saturated-fixed-work.rehearsal-seal.v1",
            "status": "PASS",
            "rehearsal_passed": True,
            "block_count": 2,
            "qa_read_only_passed": True,
            "result_scope": "NON_FORMAL_REHEARSAL_EXCLUDED_FROM_MAIN_TABLES",
            "blocks": outputs,
        },
    )


async def execute_formal_main_stage(
    *,
    repository_root: Path,
    run_root: Path,
    dependencies: LiveBlockDependencies | object,
    prepare_block: Callable[[FormalBlock], Any],
    qualification_verifier: Callable[[Path], Mapping[str, Any]] = verify_qualification_seal,
    block_executor: BlockExecutor = execute_live_block,
    episode_loader: Callable[[Path, str, str], Sequence[EpisodeInput]] = load_episode_inputs,
    source_token_counter: Callable[[Path, Sequence[EpisodeInput]], int] = _default_source_tokens,
    identity_builder: Callable[[Path, FormalBlock], ResumeIdentity] = _resume_identity,
    formal_seal_writer: Callable[[Path], Mapping[str, Any]] = write_formal_run_seal,
) -> dict[str, Any]:
    root = run_root.resolve()
    qualified = qualification_verifier(root)
    if qualified.get("verified") is not True or qualified.get("qualification_passed") is not True:
        raise StageOrchestrationError("QUALIFICATION_NOT_VERIFIED")
    _verify_rehearsal(root)
    for planned in build_formal_plan(_run_id(root)):
        block = _formal_resume_block(root, planned)
        if block is None:
            continue
        await _execute(
            repository_root=repository_root,
            stage_root=root,
            identity_root=root,
            block=block,
            dependencies=dependencies,
            prepare_block=prepare_block,
            block_executor=block_executor,
            episode_loader=episode_loader,
            source_token_counter=source_token_counter,
            identity_builder=identity_builder,
        )
    seal = dict(formal_seal_writer(root))
    if (
        seal.get("valid_construction_blocks") != 8
        or seal.get("formal_construction_calls") != 8
    ):
        raise StageOrchestrationError("FORMAL_RUN_SEAL_FAILED")
    return seal


__all__ = [
    "StageOrchestrationError",
    "execute_formal_main_stage",
    "execute_qualification_stage",
    "execute_rehearsal_stage",
]
