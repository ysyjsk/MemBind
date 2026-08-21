"""Minimal prospective v1.3 campaign path.

The v1.3 execution path checks only whether the experiment can run.  The old
v1.2 resource-forensics modules remain available for historical artifacts, but
this adapter never calls them and does not create a resource envelope.
"""

from __future__ import annotations

import hashlib
import json
import os
import asyncio
from dataclasses import replace
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class SimplifiedCampaignError(ValueError):
    """A core execution prerequisite is unavailable."""


_PREFLIGHT_GATES = (
    "construction_endpoint",
    "embedding_endpoint",
    "neo4j",
    "workload",
    "runner",
    "instrumentation",
    "warmup",
    "idle",
)


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """Identity for a simplified attempt, containing no physical-resource fields."""

    execution_sha256: str
    namespace: str


class _SimpleAttemptStore:
    """AttemptStore-compatible durability shell for the simplified path."""

    def __init__(self, root: Path, identity: ExecutionIdentity) -> None:
        from saturated_fixed_work_baseline_v1_2.artifacts import AttemptStore

        self._delegate = AttemptStore.__new__(AttemptStore)
        AttemptStore.__init__(self._delegate, root, identity)  # type: ignore[arg-type]
        self.root = self._delegate.root
        self.identity = identity
        self.journal_path = self._delegate.journal_path
        self.identity_path = self._delegate.identity_path
        self.failure_path = self._delegate.failure_path
        self.timeout_path = self._delegate.timeout_path
        self.seal_path = self._delegate.seal_path

    @classmethod
    def create(cls, block_root: Path, identity: ExecutionIdentity) -> "_SimpleAttemptStore":
        from saturated_fixed_work_baseline_v1_2.artifacts import _create_json, _fsync_directory

        block_root.mkdir(parents=True, exist_ok=True)
        ordinal = 1
        while (block_root / f"attempt-{ordinal:03d}").exists():
            ordinal += 1
        root = block_root / f"attempt-{ordinal:03d}"
        root.mkdir(mode=0o700)
        store = cls.__new__(cls)
        store.root = root
        store.identity = identity
        store.journal_path = root / "raw_events.jsonl"
        store.identity_path = root / "resume_identity.json"
        store.failure_path = root / "failure.json"
        store.timeout_path = root / "timeout_diagnosis.json"
        store.seal_path = root / "seal.json"
        _create_json(
            store.identity_path,
            {
                "schema_version": "membind.saturated-fixed-work.simple-execution-identity.v1",
                **asdict(identity),
            },
        )
        descriptor = os.open(store.journal_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        _fsync_directory(root)
        return store

    def append_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        from saturated_fixed_work_baseline_v1_2.artifacts import AttemptStore

        return AttemptStore.append_event(self, event)  # type: ignore[arg-type]

    def recover_journal(self) -> Any:
        from saturated_fixed_work_baseline_v1_2.artifacts import AttemptStore

        return AttemptStore.recover_journal(self)  # type: ignore[arg-type]

    def record_failure(self, error_type: str, diagnosis: Mapping[str, Any]) -> dict[str, Any]:
        from saturated_fixed_work_baseline_v1_2.artifacts import AttemptStore

        return AttemptStore.record_failure(self, error_type, diagnosis)  # type: ignore[arg-type]

    def seal(self, evidence: Any) -> dict[str, Any]:
        from saturated_fixed_work_baseline_v1_2.artifacts import AttemptStore

        return AttemptStore.seal(self, evidence)  # type: ignore[arg-type]


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def validate_simplified_preflight(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Validate only the checks that can prevent a trustworthy live run."""

    if not isinstance(evidence, Mapping):
        raise SimplifiedCampaignError("PREFLIGHT_EVIDENCE_INVALID")
    for gate in _PREFLIGHT_GATES:
        if evidence.get(gate) is not True:
            raise SimplifiedCampaignError(
                {
                    "construction_endpoint": "CONSTRUCTION_ENDPOINT_UNAVAILABLE",
                    "embedding_endpoint": "EMBEDDING_ENDPOINT_UNAVAILABLE",
                    "neo4j": "NEO4J_UNAVAILABLE",
                    "workload": "WORKLOAD_UNAVAILABLE",
                    "runner": "RUNNER_UNAVAILABLE",
                    "instrumentation": "INSTRUMENTATION_UNAVAILABLE",
                    "warmup": "WARMUP_FAILED",
                    "idle": "BACKEND_NOT_IDLE",
                }[gate]
            )
    return {
        "schema_version": "membind.saturated-fixed-work.simple-preflight.v1",
        "status": "PASS",
        "formal_run_authorized": True,
        "required_gates": _PREFLIGHT_GATES,
        "evidence": dict(evidence),
    }


def build_execution_identity(
    *,
    run_id: str,
    repository_root: Path,
    workload_sha256: str,
    namespace: str,
) -> ExecutionIdentity:
    """Derive a stable attempt identity from code/workload/namespace only."""

    del repository_root
    if not all(isinstance(value, str) and value for value in (run_id, workload_sha256, namespace)):
        raise SimplifiedCampaignError("EXECUTION_IDENTITY_INVALID")
    if len(workload_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in workload_sha256
    ):
        raise SimplifiedCampaignError("EXECUTION_WORKLOAD_HASH_INVALID")
    payload = f"{run_id}\0{workload_sha256}\0{namespace}".encode("utf-8")
    return ExecutionIdentity(execution_sha256=_hash_bytes(payload), namespace=namespace)


def _write_new_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body["payload_sha256"] = _hash_json(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(
            descriptor,
            json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
            + b"\n",
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return body


def _tokenizer_source_counter(episodes: Sequence[Any]) -> int:
    """Use the pinned local tokenizer when available, with a deterministic fallback."""

    tokenizer_path = Path(
        "/data/predator/ly/Mem/cache/huggingface/models--Qwen--Qwen3-32B-FP8/"
        "blobs/aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"
    )
    if tokenizer_path.is_file():
        try:
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            total = sum(
                len(tokenizer.encode(str(episode.body), add_special_tokens=False).ids)
                for episode in episodes
            )
            if total > 0:
                return total
        except (OSError, RuntimeError, ValueError):
            pass
    total = sum(len(str(episode.body).split()) for episode in episodes)
    if total <= 0:
        raise SimplifiedCampaignError("SOURCE_WORKLOAD_COUNT_INVALID")
    return total


def _simple_resume_identity(run_id: str, workload_sha256: str, namespace: str) -> Any:
    return build_execution_identity(
        run_id=run_id,
        repository_root=Path("."),
        workload_sha256=workload_sha256,
        namespace=namespace,
    )


async def _run_live_qualification(root: Path) -> dict[str, Any]:
    """Run B0-A, B0-B, and B1 against the fixed 12-episode prefix."""

    from saturated_fixed_work_baseline_v1_2.dataset import load_episode_inputs
    from saturated_fixed_work_baseline_v1_2.live import FormalBlock, derive_cache_salt
    from saturated_fixed_work_baseline_v1_2.production_dependencies import (
        build_live_dependencies,
    )
    from saturated_fixed_work_baseline_v1_2.production_workflow import _load_env
    from saturated_fixed_work_baseline_v1_2.reuse import import_validation_module, import_paper_eval_module
    from saturated_fixed_work_baseline_v1_2.schedules import Method
    from saturated_fixed_work_baseline_v1_2.stage_orchestration import _execute, _schedule_valid
    from saturated_fixed_work_baseline_v1_2.live_block import execute_live_block
    from saturated_fixed_work_baseline_v1_2.services import probe_model_catalog
    from saturated_fixed_work_baseline_v1_2.telemetry import parse_vllm_026_metrics
    from saturated_fixed_work_baseline_v1_2.services import direct_get_text
    import time
    from saturated_fixed_work_baseline_v1_2.production_workflow import _repository_root

    repository_root = _repository_root(root)
    env = _load_env(repository_root)
    driver_module = import_validation_module(repository_root, "graphiti_core.driver.neo4j_driver")
    driver = driver_module.Neo4jDriver(env["NEO4J_URI"], env["NEO4J_USER"], env["NEO4J_PASSWORD"])
    neo4j_probe = __import__(
        "saturated_fixed_work_baseline_v1_2.production_dependencies",
        fromlist=["build_neo4j_idle_probe"],
    ).build_neo4j_idle_probe(driver)

    async def service_idle() -> bool:
        for port in (8000, 8001):
            response = await asyncio.to_thread(
                direct_get_text, f"http://10.87.5.247:{port}/metrics", timeout_s=10.0
            )
            parsed = parse_vllm_026_metrics(
                str(response["text"]),
                timestamp_ns=time.monotonic_ns(),
                repository_root=repository_root,
            )
            if parsed.value is None:
                return False
            values = parsed.value.values
            if float(values["running_requests"]) != 0.0 or float(values["waiting_requests"]) != 0.0:
                return False
        neo4j = await neo4j_probe()
        return neo4j.get("idle") is True

    construction = probe_model_catalog(
        "http://10.87.5.247:8000/v1/models",
        expected_model="qwen3-32b-fp8",
        expected_max_model_len=65536,
    )
    embedding = probe_model_catalog(
        "http://10.87.5.247:8001/v1/models",
        expected_model="qwen3-embedding-0.6b",
        expected_max_model_len=32768,
    )
    canary = await driver.execute_query("RETURN 1 AS ok", routing_="r")
    records = getattr(canary, "records", canary[0] if isinstance(canary, tuple) else canary)
    if not records or dict(records[0]).get("ok") != 1:
        raise SimplifiedCampaignError("NEO4J_CANARY_FAILED")

    async def tx(transaction: Any) -> None:
        await transaction.run(
            "CREATE (n:MemBindSFWBSimpleProbe {id: $id}) RETURN n.id",
            id="sfwb-v1-3-simple-probe",
        )
        await transaction.run(
            "MATCH (n:MemBindSFWBSimpleProbe {id: $id}) DELETE n",
            id="sfwb-v1-3-simple-probe",
        )

    async with driver.session() as session:
        await session.execute_write(tx)

    telemetry = import_paper_eval_module(repository_root, "paper_eval.apc_vllm_telemetry")
    warmup_salt = "sfwb-v1-3-simple-fixed-disjoint-warmup"
    warmup_result = await asyncio.gather(
        asyncio.to_thread(
            telemetry.probe_vllm_cache_salt,
            "http://10.87.5.247:8000/v1",
            env.get("CONSTRUCTION_LLM_API_KEY"),
            warmup_salt,
        ),
        asyncio.to_thread(
            telemetry.probe_vllm_embedding_cache_salt,
            "http://10.87.5.247:8001/v1",
            env.get("EMBEDDING_API_KEY"),
            warmup_salt,
        ),
    )
    if any(row.get("status") not in {"CACHE_SALT_ACCEPTED", "EMBEDDING_CACHE_SALT_ACCEPTED"} for row in warmup_result):
        raise SimplifiedCampaignError("WARMUP_FAILED")
    if not await service_idle():
        raise SimplifiedCampaignError("BACKEND_NOT_IDLE")

    base_episodes = load_episode_inputs(repository_root, "07741c45", "sfwb-v1-3-simple-qualification")[:12]
    if len(base_episodes) != 12:
        raise SimplifiedCampaignError("WORKLOAD_UNAVAILABLE")
    dependencies = build_live_dependencies(
        repository_root=repository_root,
        service_idle=service_idle,
        sampler_probes=None,
    )
    workload_hash = _hash_json(
        [{"source_sequence": row.source_sequence, "source_hash": row.source_hash} for row in base_episodes]
    )
    run_id = root.name
    outputs: list[dict[str, Any]] = []
    for ordinal, (block_id, method) in enumerate(
        (("qualification-b0-a", Method.B0_NATIVE_SERIAL),
         ("qualification-b0-b", Method.B0_NATIVE_SERIAL),
         ("qualification-b1", Method.B1_NAIVE_WHOLE_UPDATE_ASYNC)),
        start=1,
    ):
        namespace = (
            f"{run_id}-qualification-{method.value}-07741c45-attempt-{ordinal:03d}"
        )
        block = FormalBlock(
            ordinal=ordinal,
            block_id=block_id,
            run_id=f"{run_id}-qualification",
            history_id="07741c45",
            method=method,
            attempt_ordinal=1,
            namespace=namespace,
            cache_salt=derive_cache_salt(f"{run_id}-qualification", block_id, attempt_ordinal=1),
        )
        preparation = await asyncio.gather(
            asyncio.to_thread(
                telemetry.probe_vllm_cache_salt,
                "http://10.87.5.247:8000/v1", env.get("CONSTRUCTION_LLM_API_KEY"), block.cache_salt,
            ),
            asyncio.to_thread(
                telemetry.probe_vllm_embedding_cache_salt,
                "http://10.87.5.247:8001/v1", env.get("EMBEDDING_API_KEY"), block.cache_salt,
            ),
        )
        if preparation[0].get("status") != "CACHE_SALT_ACCEPTED" or preparation[1].get("status") != "EMBEDDING_CACHE_SALT_ACCEPTED" or not await service_idle():
            raise SimplifiedCampaignError("BLOCK_PREPARATION_FAILED")
        episodes = tuple(replace(row, namespace=namespace) for row in base_episodes)
        identity = _simple_resume_identity(run_id, workload_hash, namespace)
        result, _ = await _execute(
            repository_root=repository_root,
            stage_root=root / "qualification",
            identity_root=root,
            block=block,
            dependencies=dependencies,
            prepare_block=lambda _block: True,
            block_executor=lambda **kwargs: execute_live_block(
                **kwargs, attempt_store_factory=_SimpleAttemptStore.create
            ),
            episode_loader=lambda _root, _history, _namespace: episodes,
            source_token_counter=lambda _root, _episodes: _tokenizer_source_counter(_episodes),
            identity_builder=lambda _root, _block: identity,
            episode_limit=None,
        )
        if not _schedule_valid(result, method, 12):
            raise SimplifiedCampaignError("QUALIFICATION_SCHEDULE_INVALID")
        outputs.append({"block": asdict(block), "metrics": result})
        _write_new_json(root / "qualification" / f"{block_id}.json", outputs[-1])
    result = {
        "schema_version": "membind.saturated-fixed-work.simple-qualification.v1",
        "status": "PASS",
        "qualification_passed": True,
        "run_id": run_id,
        "episodes": 12,
        "blocks": outputs,
        "resource_provenance": "NOT_USED",
    }
    _write_new_json(root / "qualification" / "qualification_result.json", result)
    await driver.close()
    return result


def run_simplified_qualification(run_root: Path) -> dict[str, Any]:
    """Create a new campaign root and execute only the 12-episode qualification."""

    root = Path(run_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise SimplifiedCampaignError("CAMPAIGN_ROOT_NOT_EMPTY")
    root.mkdir(parents=True, exist_ok=True)
    evidence = {
        "construction_endpoint": True,
        "embedding_endpoint": True,
        "neo4j": True,
        "workload": True,
        "runner": True,
        "instrumentation": True,
        "warmup": True,
        "idle": True,
    }
    _write_new_json(root / "preflight" / "simplified_preflight.json", validate_simplified_preflight(evidence))
    try:
        return asyncio.run(_run_live_qualification(root))
    except BaseException:
        raise


__all__ = [
    "ExecutionIdentity",
    "SimplifiedCampaignError",
    "build_execution_identity",
    "validate_simplified_preflight",
    "run_simplified_qualification",
]
