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

QUALIFICATION_BLOCK_IDS = (
    "qualification-b0-a",
    "qualification-b0-b",
    "qualification-b1",
    "qualification-membind",
)


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """Identity for a simplified attempt, containing no physical-resource fields."""

    execution_sha256: str
    namespace: str


@dataclass(frozen=True, slots=True)
class ExistingBaselineReference:
    baseline_root: Path
    run_id: str
    qualification_result_sha256: str
    qualification_declared_payload_sha256: str
    qualification_payload_status: str
    block_ids: tuple[str, ...]
    source_sha256s: tuple[str, ...]
    source_tokens: int
    b0_namespace: str
    b0_canonical_graph: Path
    b0_canonical_graph_sha256: str


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


def _read_json_object(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SimplifiedCampaignError(code) from None
    if not isinstance(value, dict):
        raise SimplifiedCampaignError(code)
    return value


def load_existing_baseline_reference(
    baseline_root: Path,
) -> ExistingBaselineReference:
    """Verify a completed three-block baseline for a MemBind-only extension."""

    root = Path(baseline_root).resolve()
    result = _read_json_object(
        root / "qualification/qualification_result.json",
        "BASELINE_QUALIFICATION_UNREADABLE",
    )
    result_path = root / "qualification/qualification_result.json"
    sealed = dict(result)
    observed_payload = sealed.pop("payload_sha256", None)
    payload_status = (
        "MATCH" if observed_payload == _hash_json(sealed) else "MISMATCH_DIAGNOSTIC"
    )
    blocks = result.get("blocks")
    if (
        not isinstance(observed_payload, str)
        or result.get("status") != "PASS"
        or result.get("qualification_passed") is not True
        or result.get("run_id") != root.name
        or result.get("episodes") != 12
        or not isinstance(blocks, list)
        or len(blocks) != 3
    ):
        raise SimplifiedCampaignError("BASELINE_QUALIFICATION_INVALID")
    expected = (
        ("qualification-b0-a", "B0_NATIVE_SERIAL"),
        ("qualification-b0-b", "B0_NATIVE_SERIAL"),
        ("qualification-b1", "B1_NAIVE_WHOLE_UPDATE_ASYNC"),
    )
    source_inventories: list[tuple[str, ...]] = []
    source_token_counts: list[int] = []
    b0_namespace: str | None = None
    b0_graph_path: Path | None = None
    b0_graph_sha256: str | None = None
    for (expected_block_id, expected_method), row in zip(expected, blocks, strict=True):
        if not isinstance(row, Mapping):
            raise SimplifiedCampaignError("BASELINE_BLOCK_INVALID")
        block = row.get("block")
        metrics = row.get("metrics")
        if (
            not isinstance(block, Mapping)
            or not isinstance(metrics, Mapping)
            or block.get("block_id") != expected_block_id
            or metrics.get("block_id") != expected_block_id
            or metrics.get("method") != expected_method
            or metrics.get("valid") is not True
            or metrics.get("episode_count") != 12
        ):
            raise SimplifiedCampaignError("BASELINE_BLOCK_INVALID")
        attempt_root = (
            root
            / "qualification"
            / "blocks"
            / expected_block_id
            / "attempt-001"
        )
        if Path(str(metrics.get("attempt_root", ""))).resolve() != attempt_root:
            raise SimplifiedCampaignError("BASELINE_ATTEMPT_IDENTITY_INVALID")
        metrics_document = _read_json_object(
            attempt_root / "block_metrics.json", "BASELINE_BLOCK_METRICS_UNREADABLE"
        )
        if metrics_document != {
            key: value for key, value in metrics.items() if key != "attempt_root"
        }:
            raise SimplifiedCampaignError("BASELINE_BLOCK_METRICS_DRIFT")
        seal = _read_json_object(
            attempt_root / "seal.json", "BASELINE_BLOCK_SEAL_UNREADABLE"
        )
        seal_body = dict(seal)
        seal_payload = seal_body.pop("payload_sha256", None)
        evidence = seal.get("evidence")
        if (
            seal_payload != _hash_json(seal_body)
            or seal.get("status") != "VALIDATED_SEALED"
            or not isinstance(evidence, Mapping)
            or evidence.get("episode_task_count") != 12
            or evidence.get("terminal_episode_task_count") != 12
            or evidence.get("service_idle") is not True
            or len(set(evidence.get("canonical_snapshot_hashes", ()))) != 1
        ):
            raise SimplifiedCampaignError("BASELINE_BLOCK_SEAL_INVALID")
        try:
            journal = [
                json.loads(line)
                for line in (attempt_root / "raw_events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise SimplifiedCampaignError("BASELINE_BLOCK_JOURNAL_UNREADABLE") from None
        previous = "0" * 64
        publications: list[int] = []
        for ordinal, event in enumerate(journal):
            if not isinstance(event, dict):
                raise SimplifiedCampaignError("BASELINE_BLOCK_JOURNAL_INVALID")
            event_body = dict(event)
            event_payload = event_body.pop("payload_sha256", None)
            if (
                event_body.get("ordinal") != ordinal
                or event_body.get("previous_sha256") != previous
                or event_payload != _hash_json(event_body)
            ):
                raise SimplifiedCampaignError("BASELINE_BLOCK_JOURNAL_INVALID")
            previous = str(event_payload)
            if event.get("event") == "PUBLICATION_DURABLE":
                publications.append(int(event["source_sequence"]))
        if len(publications) != 12 or sorted(publications) != list(range(12)):
            raise SimplifiedCampaignError("BASELINE_PUBLICATION_COVERAGE_INVALID")
        try:
            trace_rows = [
                json.loads(line)
                for line in (attempt_root / "native_trace.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise SimplifiedCampaignError("BASELINE_SOURCE_TRACE_UNREADABLE") from None
        trace_rows.sort(key=lambda trace: trace.get("source_sequence", -1))
        sources = tuple(trace.get("source_hash") for trace in trace_rows)
        if (
            len(trace_rows) != 12
            or [trace.get("source_sequence") for trace in trace_rows] != list(range(12))
            or any(not isinstance(value, str) or len(value) != 64 for value in sources)
        ):
            raise SimplifiedCampaignError("BASELINE_SOURCE_TRACE_INVALID")
        source_inventories.append(sources)
        source_tokens = metrics.get("source_tokens")
        if isinstance(source_tokens, bool) or not isinstance(source_tokens, int) or source_tokens <= 0:
            raise SimplifiedCampaignError("BASELINE_SOURCE_TOKENS_INVALID")
        source_token_counts.append(source_tokens)
        if expected_block_id == "qualification-b0-a":
            b0_namespace = str(metrics.get("namespace", ""))
            b0_graph_path = attempt_root / "canonical_graph.json"
            b0_graph = _read_json_object(
                b0_graph_path, "BASELINE_CANONICAL_GRAPH_UNREADABLE"
            )
            b0_graph_sha256 = _hash_json(b0_graph)
            if metrics.get("canonical_graph_hash") != b0_graph_sha256:
                raise SimplifiedCampaignError("BASELINE_CANONICAL_GRAPH_INVALID")
    if len(set(source_inventories)) != 1 or len(set(source_token_counts)) != 1:
        raise SimplifiedCampaignError("BASELINE_WORKLOAD_DRIFT")
    if not b0_namespace or b0_graph_path is None or b0_graph_sha256 is None:
        raise SimplifiedCampaignError("BASELINE_B0_REFERENCE_INVALID")
    return ExistingBaselineReference(
        baseline_root=root,
        run_id=root.name,
        qualification_result_sha256=hashlib.sha256(result_path.read_bytes()).hexdigest(),
        qualification_declared_payload_sha256=observed_payload,
        qualification_payload_status=payload_status,
        block_ids=tuple(block_id for block_id, _method in expected),
        source_sha256s=source_inventories[0],
        source_tokens=source_token_counts[0],
        b0_namespace=b0_namespace,
        b0_canonical_graph=b0_graph_path,
        b0_canonical_graph_sha256=b0_graph_sha256,
    )


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
    """Run B0-A, B0-B, B1, and MemBind on one fixed 12-episode prefix."""

    from saturated_fixed_work_baseline_v1_2.dataset import load_episode_inputs
    from saturated_fixed_work_baseline_v1_2.live import FormalBlock, derive_cache_salt
    from saturated_fixed_work_baseline_v1_3.live_dependencies import (
        build_v13_live_dependencies,
    )
    from saturated_fixed_work_baseline_v1_2.production_workflow import _load_env
    from saturated_fixed_work_baseline_v1_2.reuse import import_validation_module, import_paper_eval_module
    from saturated_fixed_work_baseline_v1_2.schedules import Method
    from saturated_fixed_work_baseline_v1_2.stage_orchestration import _execute, _schedule_valid
    from saturated_fixed_work_baseline_v1_2.live_block import execute_live_block
    from saturated_fixed_work_baseline_v1_2.services import probe_model_catalog
    from saturated_fixed_work_baseline_v1_2.telemetry import parse_vllm_026_metrics
    from saturated_fixed_work_baseline_v1_2.services import direct_get_text
    from saturated_fixed_work_baseline_v1_2.canonical_diff import canonical_diff
    from saturated_fixed_work_baseline_v1_3.membind_adapter import (
        build_membind_block_spec,
        build_production_membind_dependencies,
        execute_membind_block,
    )
    import time
    from saturated_fixed_work_baseline_v1_2.production_workflow import _repository_root

    repository_root = _repository_root(root)
    env = _load_env(repository_root)
    driver_module = import_validation_module(repository_root, "graphiti_core.driver.neo4j_driver")
    driver = driver_module.Neo4jDriver(env["NEO4J_URI"], env["NEO4J_USER"], env["NEO4J_PASSWORD"])
    from saturated_fixed_work_baseline_v1_3.live_dependencies import build_v13_neo4j_idle_probe

    neo4j_probe = build_v13_neo4j_idle_probe(driver)

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
    dependencies = build_v13_live_dependencies(
        repository_root=repository_root,
        service_idle=service_idle,
    )
    workload_hash = _hash_json(
        [{"source_sequence": row.source_sequence, "source_hash": row.source_hash} for row in base_episodes]
    )
    run_id = root.name
    outputs: list[dict[str, Any]] = []

    async def prepare_cache_salt(cache_salt: str) -> None:
        preparation = await asyncio.gather(
            asyncio.to_thread(
                telemetry.probe_vllm_cache_salt,
                "http://10.87.5.247:8000/v1",
                env.get("CONSTRUCTION_LLM_API_KEY"),
                cache_salt,
            ),
            asyncio.to_thread(
                telemetry.probe_vllm_embedding_cache_salt,
                "http://10.87.5.247:8001/v1",
                env.get("EMBEDDING_API_KEY"),
                cache_salt,
            ),
        )
        if (
            preparation[0].get("status") != "CACHE_SALT_ACCEPTED"
            or preparation[1].get("status") != "EMBEDDING_CACHE_SALT_ACCEPTED"
            or not await service_idle()
        ):
            raise SimplifiedCampaignError("BLOCK_PREPARATION_FAILED")

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
        await prepare_cache_salt(block.cache_salt)
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

    membind_namespace = (
        f"{run_id}-qualification-MEMBIND_V31-07741c45-attempt-004"
    )
    membind_cache_salt = derive_cache_salt(
        f"{run_id}-qualification", "qualification-membind", attempt_ordinal=1
    )
    membind_spec = build_membind_block_spec(
        run_id=f"{run_id}-qualification",
        namespace=membind_namespace,
        cache_salt=membind_cache_salt,
        source_sha256s=[episode.source_hash for episode in base_episodes],
    )
    await prepare_cache_salt(membind_spec.cache_salt)
    membind_episodes = tuple(
        replace(episode, namespace=membind_spec.namespace)
        for episode in base_episodes
    )
    membind_identity = _simple_resume_identity(
        run_id, workload_hash, membind_spec.namespace
    )
    membind_dependencies = build_production_membind_dependencies(
        repository_root=repository_root,
        live_dependencies=dependencies,
        attempt_store_factory=_SimpleAttemptStore.create,
    )
    membind_result = await execute_membind_block(
        repository_root=repository_root,
        run_root=root / "qualification",
        spec=membind_spec,
        identity=membind_identity,
        episodes=membind_episodes,
        source_tokens=_tokenizer_source_counter(membind_episodes),
        env=env,
        dependencies=membind_dependencies,
    )
    b0_graph = json.loads(
        (
            root
            / "qualification/blocks/qualification-b0-a/attempt-001/canonical_graph.json"
        ).read_text(encoding="utf-8")
    )
    membind_graph = json.loads(
        (Path(membind_result["attempt_root"]) / "canonical_graph.json").read_text(
            encoding="utf-8"
        )
    )
    graph_diff = canonical_diff(
        b0_graph,
        membind_graph,
        repository_root=repository_root,
        reference_namespace=str(outputs[0]["metrics"]["namespace"]),
        candidate_namespace=membind_spec.namespace,
    )
    _write_new_json(
        Path(membind_result["attempt_root"]) / "canonical_diff_vs_b0_a.json",
        graph_diff,
    )
    membind_output = {
        "block": asdict(membind_spec),
        "metrics": membind_result,
        "canonical_diff_vs_b0_a": graph_diff,
    }
    outputs.append(membind_output)
    _write_new_json(
        root / "qualification" / "qualification-membind.json",
        membind_output,
    )
    result = {
        "schema_version": "membind.saturated-fixed-work.simple-qualification.v2",
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


async def _run_live_membind_extension(
    *, root: Path, baseline: ExistingBaselineReference
) -> dict[str, Any]:
    """Run only MemBind while treating an existing sealed baseline as read-only."""

    import time

    from saturated_fixed_work_baseline_v1_2.canonical_diff import canonical_diff
    from saturated_fixed_work_baseline_v1_2.dataset import load_episode_inputs
    from saturated_fixed_work_baseline_v1_2.live import derive_cache_salt
    from saturated_fixed_work_baseline_v1_3.live_dependencies import (
        build_v13_live_dependencies,
        build_v13_neo4j_idle_probe,
    )
    from saturated_fixed_work_baseline_v1_2.production_workflow import (
        _load_env,
        _repository_root,
    )
    from saturated_fixed_work_baseline_v1_2.reuse import (
        import_paper_eval_module,
        import_validation_module,
    )
    from saturated_fixed_work_baseline_v1_2.services import (
        direct_get_text,
        probe_model_catalog,
    )
    from saturated_fixed_work_baseline_v1_2.telemetry import parse_vllm_026_metrics
    from saturated_fixed_work_baseline_v1_3.membind_adapter import (
        build_membind_block_spec,
        build_production_membind_dependencies,
        execute_membind_block,
    )

    repository_root = _repository_root(root)
    env = _load_env(repository_root)
    driver_module = import_validation_module(
        repository_root, "graphiti_core.driver.neo4j_driver"
    )
    driver = driver_module.Neo4jDriver(
        env["NEO4J_URI"], env["NEO4J_USER"], env["NEO4J_PASSWORD"]
    )
    neo4j_probe = build_v13_neo4j_idle_probe(driver)

    async def service_idle() -> bool:
        for port in (8000, 8001):
            response = await asyncio.to_thread(
                direct_get_text,
                f"http://10.87.5.247:{port}/metrics",
                timeout_s=10.0,
            )
            parsed = parse_vllm_026_metrics(
                str(response["text"]),
                timestamp_ns=time.monotonic_ns(),
                repository_root=repository_root,
            )
            if parsed.value is None:
                return False
            values = parsed.value.values
            if (
                float(values["running_requests"]) != 0.0
                or float(values["waiting_requests"]) != 0.0
            ):
                return False
        neo4j = await neo4j_probe()
        return neo4j.get("idle") is True

    try:
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
        if construction.get("status") != "PASS" or embedding.get("status") != "PASS":
            raise SimplifiedCampaignError("MODEL_ENDPOINT_UNAVAILABLE")
        canary = await driver.execute_query("RETURN 1 AS ok", routing_="r")
        records = getattr(
            canary, "records", canary[0] if isinstance(canary, tuple) else canary
        )
        if not records or dict(records[0]).get("ok") != 1:
            raise SimplifiedCampaignError("NEO4J_CANARY_FAILED")

        async def tx(transaction: Any) -> None:
            await transaction.run(
                "CREATE (n:MemBindSFWBExtensionProbe {id: $id}) RETURN n.id",
                id=f"{root.name}-probe",
            )
            await transaction.run(
                "MATCH (n:MemBindSFWBExtensionProbe {id: $id}) DELETE n",
                id=f"{root.name}-probe",
            )

        async with driver.session() as session:
            await session.execute_write(tx)

        telemetry = import_paper_eval_module(
            repository_root, "paper_eval.apc_vllm_telemetry"
        )
        warmup_salt = f"{root.name}-fixed-disjoint-warmup"
        warmup = await asyncio.gather(
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
        if (
            warmup[0].get("status") != "CACHE_SALT_ACCEPTED"
            or warmup[1].get("status") != "EMBEDDING_CACHE_SALT_ACCEPTED"
            or not await service_idle()
        ):
            raise SimplifiedCampaignError("WARMUP_OR_IDLE_FAILED")

        cache_salt = derive_cache_salt(
            root.name, "qualification-membind", attempt_ordinal=1
        )
        namespace = f"{root.name}-MEMBIND_V31-07741c45-attempt-001"
        episodes = tuple(
            replace(episode, namespace=namespace)
            for episode in load_episode_inputs(
                repository_root, "07741c45", namespace
            )[:12]
        )
        if (
            tuple(episode.source_hash for episode in episodes)
            != baseline.source_sha256s
            or _tokenizer_source_counter(episodes) != baseline.source_tokens
        ):
            raise SimplifiedCampaignError("BASELINE_EXTENSION_WORKLOAD_DRIFT")
        spec = build_membind_block_spec(
            run_id=root.name,
            namespace=namespace,
            cache_salt=cache_salt,
            source_sha256s=baseline.source_sha256s,
        )
        preparation = await asyncio.gather(
            asyncio.to_thread(
                telemetry.probe_vllm_cache_salt,
                "http://10.87.5.247:8000/v1",
                env.get("CONSTRUCTION_LLM_API_KEY"),
                spec.cache_salt,
            ),
            asyncio.to_thread(
                telemetry.probe_vllm_embedding_cache_salt,
                "http://10.87.5.247:8001/v1",
                env.get("EMBEDDING_API_KEY"),
                spec.cache_salt,
            ),
        )
        if (
            preparation[0].get("status") != "CACHE_SALT_ACCEPTED"
            or preparation[1].get("status") != "EMBEDDING_CACHE_SALT_ACCEPTED"
            or not await service_idle()
        ):
            raise SimplifiedCampaignError("BLOCK_PREPARATION_FAILED")

        live_dependencies = build_v13_live_dependencies(
            repository_root=repository_root,
            service_idle=service_idle,
        )
        membind_dependencies = build_production_membind_dependencies(
            repository_root=repository_root,
            live_dependencies=live_dependencies,
            attempt_store_factory=_SimpleAttemptStore.create,
        )
        workload_hash = _hash_json(
            [
                {
                    "source_sequence": episode.source_sequence,
                    "source_hash": episode.source_hash,
                }
                for episode in episodes
            ]
        )
        identity = _simple_resume_identity(root.name, workload_hash, namespace)
        metrics = await execute_membind_block(
            repository_root=repository_root,
            run_root=root / "qualification",
            spec=spec,
            identity=identity,
            episodes=episodes,
            source_tokens=baseline.source_tokens,
            env=env,
            dependencies=membind_dependencies,
        )
        baseline_graph = _read_json_object(
            baseline.b0_canonical_graph,
            "BASELINE_CANONICAL_GRAPH_UNREADABLE",
        )
        candidate_graph = _read_json_object(
            Path(metrics["attempt_root"]) / "canonical_graph.json",
            "MEMBIND_CANONICAL_GRAPH_UNREADABLE",
        )
        graph_diff = canonical_diff(
            baseline_graph,
            candidate_graph,
            repository_root=repository_root,
            reference_namespace=baseline.b0_namespace,
            candidate_namespace=namespace,
        )
        _write_new_json(
            Path(metrics["attempt_root"]) / "canonical_diff_vs_b0_a.json",
            graph_diff,
        )
        baseline_result = _read_json_object(
            baseline.baseline_root / "qualification/qualification_result.json",
            "BASELINE_QUALIFICATION_UNREADABLE",
        )
        comparisons = []
        for row in baseline_result["blocks"]:
            baseline_metrics = row["metrics"]
            comparisons.append(
                {
                    "block_id": row["block"]["block_id"],
                    "method": baseline_metrics["method"],
                    "build_makespan_s": baseline_metrics["build_makespan_s"],
                    "episodes_per_s": 12 / baseline_metrics["build_makespan_s"],
                    "source_tokens_per_s": baseline_metrics["source_tokens_per_s"],
                    "llm_logical_calls": baseline_metrics["llm_logical_calls"],
                    "llm_input_tokens": baseline_metrics["llm_input_tokens"],
                    "embedding_items": baseline_metrics["embedding_items"],
                    "db_writes": baseline_metrics["db_writes"],
                    "direct_semantic_violations": baseline_metrics[
                        "direct_semantic_violations"
                    ],
                    "speedup_vs_membind": baseline_metrics["build_makespan_s"]
                    / metrics["build_makespan_s"],
                }
            )
        result = {
            "schema_version": "membind.saturated-fixed-work.membind-extension.v1",
            "status": "PASS",
            "run_id": root.name,
            "baseline_run_id": baseline.run_id,
            "baseline_root_mutated": False,
            "episodes": 12,
            "metrics": metrics,
            "canonical_diff_vs_b0_a": graph_diff,
            "baseline_comparison": comparisons,
            "v5_trace_root": metrics["attempt_root"],
        }
        return _write_new_json(root / "membind_extension_result.json", result)
    finally:
        await driver.close()


def run_membind_extension(
    *, baseline_root: Path, run_root: Path
) -> dict[str, Any]:
    """Run MemBind once against an existing baseline without rerunning B0/B1."""

    baseline = load_existing_baseline_reference(baseline_root)
    root = Path(run_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise SimplifiedCampaignError("CAMPAIGN_ROOT_NOT_EMPTY")
    root.mkdir(parents=True, exist_ok=True)
    reference = {
        "schema_version": "membind.saturated-fixed-work.baseline-reference.v1",
        "status": "VERIFIED_BLOCK_SEALS",
        "baseline_run_id": baseline.run_id,
        "baseline_root": str(baseline.baseline_root),
        "baseline_root_mutated": False,
        "qualification_result_sha256": baseline.qualification_result_sha256,
        "qualification_declared_payload_sha256": (
            baseline.qualification_declared_payload_sha256
        ),
        "qualification_payload_status": baseline.qualification_payload_status,
        "block_ids": list(baseline.block_ids),
        "source_sha256s": list(baseline.source_sha256s),
        "source_tokens": baseline.source_tokens,
        "b0_namespace": baseline.b0_namespace,
        "b0_canonical_graph_sha256": baseline.b0_canonical_graph_sha256,
    }
    _write_new_json(root / "baseline_reference.json", reference)
    return asyncio.run(_run_live_membind_extension(root=root, baseline=baseline))


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
    "ExistingBaselineReference",
    "SimplifiedCampaignError",
    "build_execution_identity",
    "QUALIFICATION_BLOCK_IDS",
    "load_existing_baseline_reference",
    "run_membind_extension",
    "validate_simplified_preflight",
    "run_simplified_qualification",
]
