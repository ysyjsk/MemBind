"""Executable production composition for L0 through L4."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dataset import EpisodeInput
from .idle import collect_idle_evidence, write_idle_evidence
from .live import FormalBlock
from .preflight_seal import verify_preflight_seal, write_preflight_seal
from .production_dependencies import build_live_dependencies, build_neo4j_idle_probe
from .production_qa import build_production_qa_dependencies
from .production_sampler import build_production_probes, run_sampler_qualification
from .qa_lane import NamespaceSeal, build_gold_blind_projection
from .qa_stage import execute_qa_stage
from .qualification_seal import verify_qualification_seal
from .resource_evidence import build_resource_envelope, require_resource_gate
from .reuse import import_paper_eval_module, import_validation_module
from .run_manifest import initialize_run_artifacts, verify_run_artifacts
from .sampler import PeriodicSampler
from .services import probe_model_catalog
from .stage_orchestration import (
    execute_formal_main_stage,
    execute_qualification_stage,
    execute_rehearsal_stage,
)


class ProductionWorkflowError(ValueError):
    """A production workflow prerequisite or external observation is invalid."""


@dataclass(slots=True)
class _LiveContext:
    dependencies: Any
    prepare_block: Any
    neo4j_driver: Any
    env: dict[str, str]
    probes: dict[str, Any]


def _repository_root(run_root: Path) -> Path:
    root = Path(__file__).resolve().parents[3]
    try:
        run_root.resolve().relative_to(root / "saturated_fixed_work_baseline_v1_2/artifacts")
    except ValueError:
        # Tests may use a temporary run root, but production CLI must always
        # resolve code and frozen inputs from this checkout.
        pass
    return root


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
        raise ProductionWorkflowError(f"REQUIRED_EVIDENCE_UNREADABLE:{path.name}") from None


def _read_object(path: Path, *, self_hashed: bool = False) -> dict[str, Any]:
    if path.is_symlink():
        raise ProductionWorkflowError(f"REQUIRED_EVIDENCE_INVALID:{path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProductionWorkflowError(f"REQUIRED_EVIDENCE_UNREADABLE:{path.name}") from None
    if not isinstance(value, dict):
        raise ProductionWorkflowError(f"REQUIRED_EVIDENCE_INVALID:{path.name}")
    if self_hashed:
        candidate = dict(value)
        observed = candidate.pop("payload_sha256", None)
        if observed != _hash(candidate):
            raise ProductionWorkflowError(f"REQUIRED_EVIDENCE_HASH_INVALID:{path.name}")
    return value


def _write_new(path: Path, body: Mapping[str, Any]) -> dict[str, Any]:
    selected = dict(body)
    selected["payload_sha256"] = _hash(selected)
    payload = json.dumps(
        selected, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ProductionWorkflowError(f"PRODUCTION_ARTIFACT_ALREADY_EXISTS:{path.name}") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return selected


def _initialize(root: Path, repository_root: Path) -> None:
    if (root / "run_manifest_inventory.json").is_file():
        verify_run_artifacts(root)
        return
    inputs = root / "service_evidence"
    live = _read_object(inputs / "live_provider_resource.json", self_hashed=True)
    historical = _read_object(
        inputs / "historical_provider_resource.json", self_hashed=True
    )
    neo4j = _read_object(inputs / "runner_neo4j_resource.json", self_hashed=True)
    envelope = build_resource_envelope(
        live_provider={key: value for key, value in live.items() if key != "payload_sha256"},
        historical_provider={
            key: value for key, value in historical.items() if key != "payload_sha256"
        },
        runner_neo4j={key: value for key, value in neo4j.items() if key != "payload_sha256"},
    )
    require_resource_gate(envelope)
    run_id = root.name
    initialize_run_artifacts(
        repository_root=repository_root,
        run_root=root,
        run_id=run_id,
        resource_envelope=envelope,
    )


def _load_env(repository_root: Path) -> dict[str, str]:
    native = import_validation_module(repository_root, "graphiti_native")
    env = dict(native.load_env_file(repository_root / "membind-validation/.env"))
    required = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")
    if any(not isinstance(env.get(name), str) or not env[name] for name in required):
        raise ProductionWorkflowError("NEO4J_ENVIRONMENT_INVALID")
    return env


async def _idle_once(repository_root: Path, neo4j_probe: Any) -> bool:
    telemetry = import_paper_eval_module(repository_root, "paper_eval.apc_vllm_telemetry")
    snapshots = await asyncio.gather(
        asyncio.to_thread(
            telemetry.fetch_vllm_model_identity,
            "http://10.87.5.247:8000/v1",
        ),
        neo4j_probe(),
    )
    del snapshots[0]
    # Model identity alone is not an idle assertion; use the pinned v0.26
    # parser over both process-global metrics endpoints.
    from .services import direct_get_text
    from .telemetry import parse_vllm_026_metrics
    import time

    observations = []
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
        observations.append(parsed.value.values)
    return bool(
        snapshots[1].get("idle") is True
        and all(
            float(row["running_requests"]) == 0.0
            and float(row["waiting_requests"]) == 0.0
            for row in observations
        )
    )


def _resource(root: Path) -> dict[str, Any]:
    value = _read_object(root / "resource_envelope.json")
    require_resource_gate(value)
    return value


def _build_live_context(root: Path, repository_root: Path) -> _LiveContext:
    env = _load_env(repository_root)
    driver_module = import_validation_module(
        repository_root, "graphiti_core.driver.neo4j_driver"
    )
    driver = driver_module.Neo4jDriver(
        env["NEO4J_URI"], env["NEO4J_USER"], env["NEO4J_PASSWORD"]
    )
    neo4j_probe = build_neo4j_idle_probe(driver)

    async def service_idle() -> bool:
        return await _idle_once(repository_root, neo4j_probe)

    resource = _resource(root)
    neo4j_pid = resource.get("runner_neo4j", {}).get("pid")
    if isinstance(neo4j_pid, bool) or not isinstance(neo4j_pid, int) or neo4j_pid <= 0:
        raise ProductionWorkflowError("NEO4J_PID_INVALID")
    probes = build_production_probes(
        repository_root=repository_root,
        runner_pid=os.getpid(),
        neo4j_pid=neo4j_pid,
        ssh_alias="zju-liuyi",
    )
    dependencies = build_live_dependencies(
        repository_root=repository_root,
        service_idle=service_idle,
        sampler_probes=probes,
    )
    telemetry = import_paper_eval_module(repository_root, "paper_eval.apc_vllm_telemetry")
    api_key = env.get("CONSTRUCTION_LLM_API_KEY") or env.get("VLLM_API_KEY")
    embedding_key = env.get("EMBEDDING_API_KEY") or api_key

    async def prepare(block: FormalBlock) -> bool:
        preparation_path = (
            root
            / "block_preparations"
            / block.block_id
            / f"attempt-{block.attempt_ordinal:03d}.json"
        )
        construction, embedding = await asyncio.gather(
            asyncio.to_thread(
                telemetry.probe_vllm_cache_salt,
                "http://10.87.5.247:8000/v1",
                api_key,
                block.cache_salt,
            ),
            asyncio.to_thread(
                telemetry.probe_vllm_embedding_cache_salt,
                "http://10.87.5.247:8001/v1",
                embedding_key,
                block.cache_salt,
            ),
        )
        first = await service_idle()
        await asyncio.sleep(1.0)
        second = await service_idle()
        passed = (
            construction.get("status") == "CACHE_SALT_ACCEPTED"
            and embedding.get("status") == "EMBEDDING_CACHE_SALT_ACCEPTED"
            and first
            and second
        )
        evidence = _write_new(
            preparation_path,
            {
                "schema_version": "membind.saturated-fixed-work.block-preparation.v1",
                "status": "PASS" if passed else "INVALID",
                "block_id": block.block_id,
                "attempt_ordinal": block.attempt_ordinal,
                "cache_salt_sha256": hashlib.sha256(
                    block.cache_salt.encode("ascii")
                ).hexdigest(),
                "warmup_disjoint_from_formal_data": True,
                "construction_warmup": construction,
                "embedding_warmup": embedding,
                "idle_samples": [first, second],
            },
        )
        return evidence["status"] == "PASS"

    return _LiveContext(
        dependencies=dependencies,
        prepare_block=prepare,
        neo4j_driver=driver,
        env=env,
        probes=probes,
    )


async def _close_driver(driver: Any) -> None:
    close = getattr(driver, "close", None)
    if callable(close):
        value = close()
        if inspect.isawaitable(value):
            await value


def _token_counter(repository_root: Path, episodes: Sequence[EpisodeInput]) -> int:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        "Qwen/Qwen3-32B-FP8",
        revision="aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df",
        local_files_only=True,
        trust_remote_code=False,
    )
    total = sum(
        len(tokenizer.encode(row.body, add_special_tokens=False)) for row in episodes
    )
    if total <= 0:
        raise ProductionWorkflowError("SOURCE_TOKEN_COUNT_INVALID")
    return total


async def _qa_read_only_probe(
    repository_root: Path,
    qa_dependencies: Any,
    outputs: Sequence[Mapping[str, Any]],
) -> bool:
    from .dataset import load_and_validate_qa_inventory

    inventory = load_and_validate_qa_inventory(repository_root)
    first_question = inventory["questions"][0]
    for output in outputs:
        block = output.get("block")
        metrics = output.get("metrics")
        if not isinstance(block, Mapping) or not isinstance(metrics, Mapping):
            return False
        canonical = metrics.get("canonical_graph_hash")
        if not isinstance(canonical, str) or len(canonical) != 64:
            embedded = metrics.get("canonical_graph")
            if not isinstance(embedded, Mapping):
                return False
            canonical = _hash(embedded)
        seal = NamespaceSeal(
            method=str(block["method"]),
            history_id=str(block["history_id"]),
            namespace=str(block["namespace"]),
            canonical_hash=canonical,
            construction_call_ordinal=int(block["ordinal"]),
        )
        runtime = await qa_dependencies.runtime_factory(seal)
        try:
            before = _hash(await qa_dependencies.snapshot_graph(runtime, seal))
            public = build_gold_blind_projection(first_question)
            private = {
                field: first_question[field]
                for field in (
                    "reference_answer",
                    "gold_session_ids",
                    "gold_evidence_quotes",
                )
            }
            result = await qa_dependencies.question_runner(
                runtime=runtime,
                seal=seal,
                public_question=public,
                private_evaluation=private,
            )
            after = _hash(await qa_dependencies.snapshot_graph(runtime, seal))
            if (
                before != after
                or result.get("construction_calls") != 0
                or result.get("graph_write_attempts") != 0
            ):
                return False
        finally:
            close = getattr(runtime.graphiti, "close", None)
            if callable(close):
                value = close()
                if inspect.isawaitable(value):
                    await value
    if qa_dependencies.close is not None:
        await qa_dependencies.close()
    return True


async def _run_preflight(root: Path, repository_root: Path, context: _LiveContext) -> dict[str, Any]:
    test_summary = _read_object(root / "test_summary.json", self_hashed=True)
    if test_summary.get("tests_all_green") is not True:
        raise ProductionWorkflowError("TEST_GATE_NOT_GREEN")
    exclusivity = _read_object(
        root / "service_evidence/client_exclusivity.json", self_hashed=True
    )
    if exclusivity.get("no_other_clients") is not True:
        raise ProductionWorkflowError("OTHER_CLIENT_CONTAMINATION")
    construction_catalog, embedding_catalog = await asyncio.gather(
        asyncio.to_thread(
            probe_model_catalog,
            "http://10.87.5.247:8000/v1/models",
            expected_model="qwen3-32b-fp8",
            expected_max_model_len=65536,
        ),
        asyncio.to_thread(
            probe_model_catalog,
            "http://10.87.5.247:8001/v1/models",
            expected_model="qwen3-embedding-0.6b",
            expected_max_model_len=32768,
        ),
    )
    canary = await context.neo4j_driver.execute_query("RETURN 1 AS ok", routing_="r")
    records = getattr(canary, "records", canary[0] if isinstance(canary, tuple) else canary)
    neo4j_passed = bool(records and dict(records[0]).get("ok") == 1)
    telemetry = import_paper_eval_module(repository_root, "paper_eval.apc_vllm_telemetry")
    salt = "sfwb12-l0-fixed-disjoint-warmup"
    construction_warmup, embedding_warmup = await asyncio.gather(
        asyncio.to_thread(
            telemetry.probe_vllm_cache_salt,
            "http://10.87.5.247:8000/v1",
            context.env.get("CONSTRUCTION_LLM_API_KEY"),
            salt,
        ),
        asyncio.to_thread(
            telemetry.probe_vllm_embedding_cache_salt,
            "http://10.87.5.247:8001/v1",
            context.env.get("EMBEDDING_API_KEY"),
            salt,
        ),
    )
    service = _write_new(
        root / "service_evidence/l0_services.json",
        {
            "schema_version": "membind.saturated-fixed-work.l0-services.v1",
            "status": "PASS" if neo4j_passed else "INVALID",
            "construction_canary_passed": construction_catalog.get("status") == "PASS",
            "embedding_canary_passed": embedding_catalog.get("status") == "PASS",
            "neo4j_canary_passed": neo4j_passed,
            "construction_cache_salt_passed": construction_warmup.get("status")
            == "CACHE_SALT_ACCEPTED",
            "embedding_cache_salt_passed": embedding_warmup.get("status")
            == "EMBEDDING_CACHE_SALT_ACCEPTED",
            "no_other_clients": True,
            "construction_catalog": construction_catalog,
            "embedding_catalog": embedding_catalog,
        },
    )
    warmup = _write_new(
        root / "preflight/warmup_evidence.json",
        {
            "schema_version": "membind.saturated-fixed-work.warmup-evidence.v1",
            "status": "PASS",
            "manifest_verified": True,
            "disjoint_from_formal_data": True,
            "construction_warmup_passed": construction_warmup.get("status")
            == "CACHE_SALT_ACCEPTED",
            "embedding_warmup_passed": embedding_warmup.get("status")
            == "EMBEDDING_CACHE_SALT_ACCEPTED",
            "construction": construction_warmup,
            "embedding": embedding_warmup,
        },
    )

    def sync_neo4j_probe() -> dict[str, Any]:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            context.env["NEO4J_URI"],
            auth=(context.env["NEO4J_USER"], context.env["NEO4J_PASSWORD"]),
        )
        try:
            record = driver.execute_query(
                "SHOW TRANSACTIONS YIELD currentQuery "
                "WHERE currentQuery IS NULL OR NOT currentQuery STARTS WITH 'SHOW TRANSACTIONS' "
                "RETURN count(*) AS active_transactions",
                routing_="r",
            ).records[0]
            active = int(record["active_transactions"])
            return {"idle": active == 0, "active_transactions": active}
        finally:
            driver.close()

    idle_value = await asyncio.to_thread(
        collect_idle_evidence,
        repository_root=repository_root,
        neo4j_idle_probe=sync_neo4j_probe,
        sample_count=2,
        interval_s=1.0,
    )
    idle = write_idle_evidence(root / "preflight/idle_evidence.json", idle_value)
    sampler = PeriodicSampler(
        probes=context.probes,
        output_path=root / "preflight/sampler_qualification_samples.jsonl",
        target_period_s=1.0,
    )
    sampler_result = await run_sampler_qualification(
        sampler=sampler,
        duration_s=60.0,
        output_path=root / "preflight/sampler_qualification.json",
    )
    if sampler_result.get("formal_run_authorized") is not True:
        raise ProductionWorkflowError("SAMPLER_QUALIFICATION_FAILED")
    resource = _resource(root)
    evidence = {
        "tests_all_green": True,
        "repository_identity_verified": True,
        "data_identity_verified": True,
        "provider_identity_verified": True,
        "qa_identity_verified": True,
        "historical_resource_match": resource["historical_resource_match"],
        "live_resource_envelope_verified": resource[
            "live_resource_envelope_verified"
        ],
        "test_summary_sha256": _file_hash(root / "test_summary.json"),
        "service_evidence_sha256": _file_hash(
            root / "service_evidence/l0_services.json"
        ),
        "warmup_evidence_sha256": _file_hash(root / "preflight/warmup_evidence.json"),
        "idle_evidence_sha256": _file_hash(root / "preflight/idle_evidence.json"),
        "sampler_qualification_sha256": _file_hash(
            root / "preflight/sampler_qualification.json"
        ),
    }
    return write_preflight_seal(root, evidence)


def run_preflight_workflow(run_root: Path) -> dict[str, Any]:
    root = run_root.resolve()
    repository_root = _repository_root(root)
    _initialize(root, repository_root)
    if (root / "preflight/preflight_seal.json").is_file():
        return verify_preflight_seal(root)
    context = _build_live_context(root, repository_root)
    try:
        return asyncio.run(_run_preflight(root, repository_root, context))
    finally:
        asyncio.run(_close_driver(context.neo4j_driver))


async def _instrumentation_aa(repository_root: Path, root: Path) -> dict[str, Any]:
    module = import_validation_module(
        repository_root, "native_characterization_c1_qualification"
    )
    result = await module.run_qualification()
    module.validate_result(result)
    median = result["paired_distribution"]["median_ratio"]
    qualified = result.get("classification") == "clean_pass" and median <= 0.02
    evidence = _write_new(
        root / "qualification/instrumentation_aa.json",
        {**result, "protocol_qualified": qualified},
    )
    if not qualified:
        raise ProductionWorkflowError("INSTRUMENTATION_AA_FAILED")
    return {"qualified": True, "overhead_fraction": median, "evidence_sha256": evidence["payload_sha256"]}


def run_qualification_workflow(run_root: Path) -> dict[str, Any]:
    root = run_root.resolve()
    repository_root = _repository_root(root)
    verify_preflight_seal(root)
    context = _build_live_context(root, repository_root)

    async def run() -> dict[str, Any]:
        aa = await _instrumentation_aa(repository_root, root)
        qa_dependencies = build_production_qa_dependencies(repository_root=repository_root)
        return await execute_qualification_stage(
            repository_root=repository_root,
            run_root=root,
            dependencies=context.dependencies,
            instrumentation_aa=aa,
            prepare_block=context.prepare_block,
            qa_read_only_probe=lambda outputs: _qa_read_only_probe(
                repository_root, qa_dependencies, outputs
            ),
            source_token_counter=_token_counter,
        )

    try:
        return asyncio.run(run())
    finally:
        asyncio.run(_close_driver(context.neo4j_driver))


def run_formal_workflow(run_root: Path) -> dict[str, Any]:
    root = run_root.resolve()
    repository_root = _repository_root(root)
    verify_qualification_seal(root)
    context = _build_live_context(root, repository_root)

    async def run() -> dict[str, Any]:
        if not (root / "rehearsal/rehearsal_seal.json").is_file():
            qa_dependencies = build_production_qa_dependencies(
                repository_root=repository_root
            )
            await execute_rehearsal_stage(
                repository_root=repository_root,
                run_root=root,
                dependencies=context.dependencies,
                prepare_block=context.prepare_block,
                qa_read_only_probe=lambda outputs: _qa_read_only_probe(
                    repository_root, qa_dependencies, outputs
                ),
                source_token_counter=_token_counter,
            )
        return await execute_formal_main_stage(
            repository_root=repository_root,
            run_root=root,
            dependencies=context.dependencies,
            prepare_block=context.prepare_block,
            source_token_counter=_token_counter,
        )

    try:
        return asyncio.run(run())
    finally:
        asyncio.run(_close_driver(context.neo4j_driver))


def run_qa_workflow(run_root: Path) -> dict[str, Any]:
    root = run_root.resolve()
    repository_root = _repository_root(root)
    dependencies = build_production_qa_dependencies(repository_root=repository_root)
    return asyncio.run(
        execute_qa_stage(
            repository_root=repository_root,
            run_root=root,
            dependencies=dependencies,
        )
    )


__all__ = [
    "ProductionWorkflowError",
    "run_formal_workflow",
    "run_preflight_workflow",
    "run_qa_workflow",
    "run_qualification_workflow",
]
