"""Production workload, identity, preflight, and passive telemetry for S6."""

from __future__ import annotations

import importlib
import inspect
import json
import re
import sys
import time
import urllib.request
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256, sha256_file
from .s5_mstar_production_core_identity import (
    verify_s5_mstar_production_core_identity,
)
from .s5_a0_controller import close_s5_a0_runtime, ensure_s5_a0_runtime_ready
from .s5_graphiti_native_binding import (
    build_native_add_episode_callable,
    load_graphiti_native_binding,
)
from .s5_graphiti_semantic_binding import load_graphiti_semantic_binding
from .s5_native_method_adapters import S5EpisodeRef
from .s6_block_controller import S6BlockControllerPaths, S6BlockRuntime
from .s6_block_result import verify_s6_work_volume
from .s6_calibration_contract import verify_s6_cell_identity, verify_s6_matrix_freeze
from .s6_graphiti_mstar_adapter import (
    S6MStarLiveSemanticAdapter,
    materialize_s6_mstar_sources,
)
from .s6_live_authority import (
    build_s6_live_authority,
    evaluate_s6_live_preflight,
    finalize_s6_live_authority,
    finalize_s6_live_preflight,
    verify_s6_live_authority_consumption,
)


_PROJECT = Path(__file__).resolve().parents[2]
_ROOT = _PROJECT.parent
_LEGACY = _ROOT / "membind-validation"
_LEGACY_SRC = _LEGACY / "src"
_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
    "longmemeval_s_cleaned.json"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WRITE_QUERY = re.compile(
    r"\b(?:CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH)\b",
    re.IGNORECASE,
)


class S6ProductionError(ValueError):
    """A production workload, identity, or read-only preparation failed."""


def _fail(code: str) -> S6ProductionError:
    return S6ProductionError(code)


@dataclass(frozen=True)
class S6ProductionPaths:
    dataset: Path = _DATASET
    dataset_builder: Path = _LEGACY_SRC / "dataset.py"
    legacy_src: Path = _LEGACY_SRC
    env_file: Path = _LEGACY / ".env"
    production_core_identity: Path = (
        _PROJECT
        / "artifacts/paper_eval/native/"
        "S5_MSTAR_PRODUCTION_CORE_IDENTITY_CURRENT_HEAD_V2_20260816.json"
    )


@dataclass(frozen=True)
class S6BlockPreparationPaths:
    matrix_freeze: Path
    preflight: Path
    authority: Path


ObservationCollector = Callable[
    [dict[str, object]], Mapping[str, object] | Awaitable[Mapping[str, object]]
]
HttpGetJson = Callable[[str, Mapping[str, str]], Mapping[str, object] | Awaitable[Mapping[str, object]]]
Neo4jCounter = Callable[[str], tuple[int, int] | Awaitable[tuple[int, int]]]


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _hashes(value: Mapping[str, str], code: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise _fail(code)
    return {
        str(key): _sha(item, code)
        for key, item in sorted(value.items())
        if isinstance(key, str) and key
    }


def _load(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _import_exact(module_name: str, path: Path) -> object:
    source_root = str(Path(path).resolve().parent)
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    try:
        module = importlib.import_module(module_name)
    except Exception:
        raise _fail(f"{module_name}_import_failed") from None
    if Path(str(getattr(module, "__file__", ""))).resolve() != Path(path).resolve():
        raise _fail(f"{module_name}_source_drift")
    return module


def load_s6_sources(
    *,
    cell: Mapping[str, object],
    paths: S6ProductionPaths,
    epoch_clock_ns: Callable[[], int] = time.time_ns,
) -> tuple[object, ...]:
    """Materialize one frozen LongMemEval history and rebind only its namespace."""

    try:
        selected_cell = verify_s6_cell_identity(cell)
    except Exception:
        raise _fail("cell_identity_invalid") from None
    if not isinstance(paths, S6ProductionPaths):
        raise _fail("production_paths_invalid")
    dataset = _import_exact("dataset", paths.dataset_builder)
    for name in ("load_json_records", "records_by_question_id", "build_episodes"):
        if not callable(getattr(dataset, name, None)):
            raise _fail("dataset_builder_api_invalid")
    try:
        records = dataset.records_by_question_id(
            dataset.load_json_records(paths.dataset)
        )
        history = records[str(selected_cell["history_id"])]
        native = tuple(dataset.build_episodes(history))
    except Exception:
        raise _fail("frozen_workload_load_failed") from None
    namespace = str(selected_cell["namespace"])
    rebound: list[object] = []
    for index, episode in enumerate(native):
        try:
            selected = replace(episode, group_id=namespace)
        except Exception:
            raise _fail("frozen_episode_rebind_failed") from None
        if (
            getattr(selected, "source_sequence", None) != index
            or getattr(selected, "group_id", None) != namespace
            or _SHA256.fullmatch(str(getattr(selected, "source_hash", ""))) is None
        ):
            raise _fail("frozen_workload_identity_invalid")
        rebound.append(selected)
    if not rebound:
        raise _fail("frozen_workload_empty")
    if selected_cell["method"] == "P*":
        return tuple(
            S5EpisodeRef(index, str(episode.source_hash), episode)
            for index, episode in enumerate(rebound)
        )
    return materialize_s6_mstar_sources(
        tuple(rebound),
        namespace=namespace,
        epoch_clock_ns=epoch_clock_ns,
    )


def build_s6_execution_identity(
    *,
    cell: Mapping[str, object],
    source_sha256: Mapping[str, str],
    dependency_sha256: Mapping[str, str],
    production_core_identity_sha256: str | None = None,
) -> str:
    """Build a method/C identity that deliberately excludes the history ID."""

    try:
        selected_cell = verify_s6_cell_identity(cell)
    except Exception:
        raise _fail("cell_identity_invalid") from None
    method = str(selected_cell["method"])
    if method == "M*":
        core = _sha(
            production_core_identity_sha256,
            "production_core_identity_invalid",
        )
    elif production_core_identity_sha256 is not None:
        raise _fail("pstar_production_core_identity_forbidden")
    else:
        core = None
    payload = {
        "schema_version": "membind.paper-eval-v3.s6-execution-identity.v1",
        "method": method,
        "configured_concurrency": selected_cell["configured_concurrency"],
        "construction": {
            "served_model_id": "qwen3-32b-fp8",
            "vllm_version": "0.26.0",
            "max_model_len": 65536,
            "structured_output_mode": "json_schema",
            "requested_max_tokens": 16384,
            "enable_thinking": False,
        },
        "embedding": {"served_model_id": "qwen3-embedding-0.6b"},
        "production_core_identity_sha256": core,
        "source_sha256": _hashes(source_sha256, "source_identity_invalid"),
        "dependency_sha256": _hashes(
            dependency_sha256, "dependency_identity_invalid"
        ),
    }
    return payload_sha256(payload)


def current_s6_source_closure(method: str) -> dict[str, str]:
    paths = {
        "authority": Path(__file__).with_name("s6_live_authority.py"),
        "calibration_contract": Path(__file__).with_name(
            "s6_calibration_contract.py"
        ),
        "block_controller": Path(__file__).with_name("s6_block_controller.py"),
        "method_runner": Path(__file__).with_name(
            "s6_pstar_grid.py" if method == "P*" else "s6_mstar_grid.py"
        ),
        "block_postprocess": Path(__file__).with_name(
            "s6_block_postprocess.py"
        ),
        "authority_test": _PROJECT / "tests/test_s6_live_authority.py",
        "production_runtime": Path(__file__).resolve(),
    }
    result = {key: sha256_file(path) for key, path in sorted(paths.items())}
    if any(value == "missing" for value in result.values()):
        raise _fail("source_closure_unavailable")
    return result


def current_s6_dependency_closure(method: str, paths: S6ProductionPaths) -> dict[str, str]:
    dependencies = {
        "dataset_builder": paths.dataset_builder,
        "graphiti_native": paths.legacy_src / "graphiti_native.py",
        "native_characterization_runtime": (
            paths.legacy_src / "native_characterization_runtime.py"
        ),
        "s5_graphiti_native_binding": Path(__file__).with_name(
            "s5_graphiti_native_binding.py"
        ),
    }
    if method == "M*":
        dependencies.update(
            {
                "s6_graphiti_mstar_adapter": Path(__file__).with_name(
                    "s6_graphiti_mstar_adapter.py"
                ),
                "s5_graphiti_mstar_semantics": Path(__file__).with_name(
                    "s5_graphiti_mstar_semantics.py"
                ),
                "s5_graphiti_semantic_binding": Path(__file__).with_name(
                    "s5_graphiti_semantic_binding.py"
                ),
            }
        )
    result = {key: sha256_file(path) for key, path in sorted(dependencies.items())}
    if any(value == "missing" for value in result.values()):
        raise _fail("dependency_closure_unavailable")
    return result


async def prepare_s6_block_authority(
    *,
    paths: S6BlockPreparationPaths,
    cell_index: int,
    sources: Sequence[object],
    git_commit: str,
    observation_collector: ObservationCollector,
    production_paths: S6ProductionPaths = S6ProductionPaths(),
) -> dict[str, object]:
    """Persist a read-only preflight, then one authority only on PASS."""

    if not isinstance(paths, S6BlockPreparationPaths):
        raise _fail("preparation_paths_invalid")
    if paths.preflight.exists() or paths.authority.exists():
        raise _fail("preparation_output_exists")
    if not callable(observation_collector):
        raise _fail("observation_collector_invalid")
    try:
        freeze = verify_s6_matrix_freeze(_load(paths.matrix_freeze, "matrix_invalid"))
        cell = verify_s6_cell_identity(freeze["payload"]["cells"][cell_index])
    except Exception:
        raise _fail("matrix_or_cell_invalid") from None
    source_hashes = tuple(str(getattr(source, "source_sha256", "")) for source in sources)
    if not source_hashes or any(_SHA256.fullmatch(item) is None for item in source_hashes):
        raise _fail("sources_invalid")
    source_closure = current_s6_source_closure(str(cell["method"]))
    dependency_closure = current_s6_dependency_closure(
        str(cell["method"]), production_paths
    )
    core_identity: str | None = None
    if cell["method"] == "M*":
        try:
            core = verify_s5_mstar_production_core_identity(
                _load(
                    production_paths.production_core_identity,
                    "production_core_identity_invalid",
                )
            )
            core_identity = str(core["identity_sha256"])
        except Exception:
            raise _fail("production_core_identity_invalid") from None
    execution_identity = build_s6_execution_identity(
        cell=cell,
        source_sha256=source_closure,
        dependency_sha256=dependency_closure,
        production_core_identity_sha256=core_identity,
    )
    observations_value = observation_collector(deepcopy(dict(cell)))
    observations = (
        await observations_value
        if inspect.isawaitable(observations_value)
        else observations_value
    )
    if not isinstance(observations, Mapping):
        raise _fail("observations_invalid")
    matrix_file_sha = sha256_file(paths.matrix_freeze)
    evaluation = evaluate_s6_live_preflight(
        matrix_freeze=freeze,
        matrix_file_sha256=matrix_file_sha,
        cell_index=cell_index,
        episode_source_sha256s=source_hashes,
        execution_identity_sha256=execution_identity,
        observations=observations,
    )
    preflight = finalize_s6_live_preflight(
        output_path=paths.preflight,
        evaluation=evaluation,
        git_commit=git_commit,
    )
    if preflight["payload"]["verdict"] != "PASS":
        raise _fail("preflight_not_pass")
    draft = build_s6_live_authority(
        matrix_freeze=freeze,
        matrix_file_sha256=matrix_file_sha,
        cell_index=cell_index,
        episode_source_sha256s=source_hashes,
        preflight=preflight,
        preflight_file_sha256=sha256_file(paths.preflight),
        execution_identity_sha256=execution_identity,
        source_sha256=source_closure,
    )
    authority = finalize_s6_live_authority(
        output_path=paths.authority,
        authority=draft["payload"],
        git_commit=git_commit,
    )
    return {
        "status": authority["payload"]["status"],
        "cell": cell,
        "execution_identity_sha256": execution_identity,
        "preflight_payload_sha256": preflight["payload_sha256"],
        "authority_payload_sha256": authority["payload_sha256"],
    }


def instrument_s6_runtime(
    runtime: object, *, episode_key: Callable[[], object] = lambda: None
) -> None:
    """Install idempotent passive embedding and query counters."""

    graphiti = getattr(runtime, "graphiti", None)
    if graphiti is None:
        raise _fail("runtime_graphiti_missing")
    embedder = getattr(graphiti, "embedder", None) or getattr(runtime, "embedder", None)
    if embedder is not None and not getattr(embedder, "_s6_counting_installed", False):
        events: list[dict[str, object]] = []
        original_create = getattr(embedder, "create", None)
        original_batch = getattr(embedder, "create_batch", None)
        if callable(original_create):

            async def counted_create(value: object, *args: object, **kwargs: object) -> object:
                result = original_create(value, *args, **kwargs)
                result = await result if inspect.isawaitable(result) else result
                events.append({"episode_key": episode_key(), "text_count": 1})
                return result

            embedder.create = counted_create
        if callable(original_batch):

            async def counted_batch(values: object, *args: object, **kwargs: object) -> object:
                result = original_batch(values, *args, **kwargs)
                result = await result if inspect.isawaitable(result) else result
                count = len(values) if isinstance(values, Sequence) else 0
                events.append({"episode_key": episode_key(), "text_count": count})
                return result

            embedder.create_batch = counted_batch
        embedder.call_events = events
        embedder._s6_counting_installed = True
    driver = getattr(graphiti, "driver", None)
    if (
        driver is not None
        and callable(getattr(driver, "execute_query", None))
        and not getattr(driver, "_s6_counting_installed", False)
    ):
        original_query = driver.execute_query
        query_events: list[dict[str, object]] = []

        async def counted_query(query: object, *args: object, **kwargs: object) -> object:
            kind = "write" if _WRITE_QUERY.search(str(query)) else "query"
            query_events.append({"episode_key": episode_key(), "kind": kind})
            result = original_query(query, *args, **kwargs)
            return await result if inspect.isawaitable(result) else result

        driver.execute_query = counted_query
        driver.query_events = query_events
        driver._s6_counting_installed = True


def snapshot_s6_work_volume(runtime: object) -> dict[str, int | None]:
    graphiti = getattr(runtime, "graphiti", None)
    if graphiti is None:
        raise _fail("runtime_graphiti_missing")
    llm = getattr(runtime, "llm_client", None) or getattr(graphiti, "llm_client", None)
    llm = getattr(llm, "inner", llm)
    llm_events = getattr(llm, "call_events", ()) or ()
    embedding = getattr(graphiti, "embedder", None) or getattr(runtime, "embedder", None)
    embedding_events = getattr(embedding, "call_events", ()) or ()
    query_events = getattr(getattr(graphiti, "driver", None), "query_events", ()) or ()

    def token_total(name: str) -> int:
        total = 0
        for event in llm_events:
            usage = event.get("token_usage", {}) if isinstance(event, Mapping) else {}
            value = usage.get(name, 0) if isinstance(usage, Mapping) else 0
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                total += value
        return total

    result = {
        "llm_call_count": len(llm_events),
        "llm_prompt_tokens": token_total("prompt_tokens"),
        "llm_completion_tokens": token_total("completion_tokens"),
        "embedding_call_count": len(embedding_events),
        "embedding_input_count": sum(
            int(event.get("text_count", 0))
            for event in embedding_events
            if isinstance(event, Mapping)
        ),
        "db_query_count": sum(
            isinstance(event, Mapping) and event.get("kind") == "query"
            for event in query_events
        ),
        "db_transaction_count": None,
        "db_write_count": sum(
            isinstance(event, Mapping) and event.get("kind") == "write"
            for event in query_events
        ),
    }
    return verify_s6_work_volume(result)


def _model_record(value: Mapping[str, object], code: str) -> dict[str, object]:
    rows = value.get("data")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise _fail(code)
    return dict(rows[0])


async def _default_http_get_json(
    url: str, headers: Mapping[str, str]
) -> Mapping[str, object]:
    def request() -> Mapping[str, object]:
        selected = urllib.request.Request(url, headers=dict(headers), method="GET")
        with urllib.request.urlopen(selected, timeout=15) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, Mapping):
            raise _fail("service_response_invalid")
        return value

    import asyncio

    return await asyncio.to_thread(request)


async def _default_neo4j_counter(namespace: str) -> tuple[int, int]:
    try:
        from neo4j import AsyncGraphDatabase
        import os

        driver = AsyncGraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
        )
        async with driver:
            async with driver.session(database="neo4j") as session:
                nodes_result = await session.run(
                    "MATCH (n) WHERE n.group_id = $namespace RETURN count(n) AS count",
                    namespace=namespace,
                )
                relationships_result = await session.run(
                    "MATCH ()-[r]->() WHERE r.group_id = $namespace "
                    "RETURN count(r) AS count",
                    namespace=namespace,
                )
                nodes = await nodes_result.single(strict=True)
                relationships = await relationships_result.single(strict=True)
        return int(nodes["count"]), int(relationships["count"])
    except Exception:
        raise _fail("neo4j_read_only_probe_failed") from None


async def collect_s6_read_only_observations(
    cell: Mapping[str, object],
    *,
    paths: S6ProductionPaths = S6ProductionPaths(),
    http_get_json: HttpGetJson = _default_http_get_json,
    neo4j_counter: Neo4jCounter = _default_neo4j_counter,
) -> dict[str, object]:
    """Collect only service identity/readiness and namespace 0/0 counts."""

    try:
        selected_cell = verify_s6_cell_identity(cell)
    except Exception:
        raise _fail("cell_identity_invalid") from None
    graphiti_native = _import_exact("graphiti_native", paths.legacy_src / "graphiti_native.py")
    loader = getattr(graphiti_native, "load_env_file", None)
    if not callable(loader) or not isinstance(loader(paths.env_file), Mapping):
        raise _fail("environment_invalid")
    import os

    construction_base = os.environ.get("CONSTRUCTION_LLM_BASE_URL", "").rstrip("/")
    embedding_base = os.environ.get("EMBEDDING_BASE_URL", "").rstrip("/")
    construction_key = os.environ.get("CONSTRUCTION_LLM_API_KEY", "")
    embedding_key = os.environ.get("EMBEDDING_API_KEY", "")
    if not construction_base or not embedding_base:
        raise _fail("service_base_url_missing")
    construction_headers = {"Authorization": f"Bearer {construction_key}"}
    embedding_headers = {"Authorization": f"Bearer {embedding_key}"}
    try:
        construction_raw = http_get_json(
            f"{construction_base}/models", construction_headers
        )
        construction_models = (
            await construction_raw if inspect.isawaitable(construction_raw) else construction_raw
        )
        version_url = re.sub(r"/v1$", "", construction_base) + "/version"
        version_raw = http_get_json(version_url, construction_headers)
        version = await version_raw if inspect.isawaitable(version_raw) else version_raw
        embedding_raw = http_get_json(f"{embedding_base}/models", embedding_headers)
        embedding_models = (
            await embedding_raw if inspect.isawaitable(embedding_raw) else embedding_raw
        )
        construction = _model_record(construction_models, "construction_models_invalid")
        embedding = _model_record(embedding_models, "embedding_models_invalid")
        if not isinstance(version, Mapping):
            raise _fail("construction_version_invalid")
        counts_value = neo4j_counter(str(selected_cell["namespace"]))
        counts = await counts_value if inspect.isawaitable(counts_value) else counts_value
        if (
            not isinstance(counts, tuple)
            or len(counts) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in counts)
        ):
            raise _fail("neo4j_counts_invalid")
    except S6ProductionError:
        raise
    except Exception:
        raise _fail("read_only_service_probe_failed") from None
    max_model_len = construction.get("max_model_len")
    if not isinstance(max_model_len, int):
        # vLLM's OpenAI model card may expose this under the nested limits map.
        limits = construction.get("limits")
        max_model_len = limits.get("max_model_len") if isinstance(limits, Mapping) else None
    return {
        "construction": {
            "status": "PASS",
            "served_model_id": construction.get("id"),
            "vllm_version": version.get("version"),
            "max_model_len": max_model_len,
        },
        "embedding": {
            "status": "PASS",
            "served_model_id": embedding.get("id"),
        },
        "neo4j_connectivity": True,
        "namespace": selected_cell["namespace"],
        "namespace_state": {
            "node_count": counts[0],
            "relationship_count": counts[1],
        },
    }


async def build_s6_block_runtime(
    *,
    cell: Mapping[str, object],
    controller_paths: S6BlockControllerPaths,
    paths: S6ProductionPaths = S6ProductionPaths(),
) -> S6BlockRuntime:
    """Construct the exact Graphiti runtime only after authority consumption."""

    try:
        selected_cell = verify_s6_cell_identity(cell)
        consumption = verify_s6_live_authority_consumption(
            _load(controller_paths.consumption, "authority_consumption_invalid")
        )
    except Exception:
        raise _fail("authority_consumption_invalid") from None
    if consumption["payload"]["cell"] != selected_cell:
        raise _fail("authority_consumption_cell_mismatch")
    graphiti_native = _import_exact("graphiti_native", paths.legacy_src / "graphiti_native.py")
    loader = getattr(graphiti_native, "load_env_file", None)
    if not callable(loader) or not isinstance(loader(paths.env_file), Mapping):
        raise _fail("environment_invalid")
    runtime_module = _import_exact(
        "native_characterization_runtime",
        paths.legacy_src / "native_characterization_runtime.py",
    )
    builder = getattr(runtime_module, "build_u0_graphiti_from_env", None)
    if not callable(builder):
        raise _fail("runtime_builder_missing")

    def consumed_checker(_action: object) -> dict[str, object]:
        current = verify_s6_live_authority_consumption(
            _load(controller_paths.consumption, "authority_consumption_invalid")
        )
        if current["payload"]["cell"] != selected_cell:
            raise _fail("authority_consumption_cell_mismatch")
        return {"status": "S6_AUTHORITY_CONSUMED"}

    loader(paths.env_file)
    try:
        runtime = builder(
            authorization_checker=consumed_checker,
            live_action="native_characterization_c0",
            env_loader=lambda: None,
            structured_output_mode="json_schema",
        )
        await ensure_s5_a0_runtime_ready(runtime)
    except Exception:
        raise _fail("runtime_construction_or_readiness_failed") from None
    instrumentation = _import_exact(
        "instrumentation", paths.legacy_src / "instrumentation.py"
    )
    episode_scope = getattr(instrumentation, "episode_scope", None)
    current_episode_key = getattr(instrumentation, "current_episode_key", None)
    if not callable(episode_scope) or not callable(current_episode_key):
        raise _fail("episode_instrumentation_missing")
    instrument_s6_runtime(runtime, episode_key=current_episode_key)
    graphiti = getattr(runtime, "graphiti", None)
    if graphiti is None:
        raise _fail("runtime_graphiti_missing")

    async def close() -> None:
        await close_s5_a0_runtime(runtime)

    if selected_cell["method"] == "P*":
        binding = load_graphiti_native_binding(module_loader=lambda _name: graphiti_native)
        invoke = build_native_add_episode_callable(graphiti=graphiti, binding=binding)

        async def native_add_episode(episode: object) -> object:
            source_sequence = getattr(episode, "source_sequence", None)
            if isinstance(source_sequence, bool) or not isinstance(source_sequence, int):
                raise _fail("episode_source_sequence_invalid")
            with episode_scope(str(selected_cell["run_id"]), source_sequence):
                return await invoke(episode)

        return S6BlockRuntime(
            native_add_episode=native_add_episode,
            work_volume_snapshot=lambda: snapshot_s6_work_volume(runtime),
            close=close,
        )

    try:
        core = verify_s5_mstar_production_core_identity(
            _load(paths.production_core_identity, "production_core_identity_invalid")
        )
        native_binding = load_graphiti_native_binding(
            module_loader=lambda _name: graphiti_native
        )
        semantic_binding = load_graphiti_semantic_binding()
        from graphiti_core.nodes import EpisodicNode

        adapter = S6MStarLiveSemanticAdapter(
            graphiti=graphiti,
            semantic_binding=semantic_binding,
            graphiti_episode_kwargs=native_binding.graphiti_episode_kwargs,
            episodic_node_type=EpisodicNode,
        )
    except Exception:
        raise _fail("mstar_runtime_composition_failed") from None

    async def semantic_prepare(source: object, logical_time_ns: int) -> object:
        sequence = getattr(source, "source_sequence", None)
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise _fail("mstar_source_sequence_invalid")
        with episode_scope(str(selected_cell["run_id"]), sequence):
            return await adapter.prepare(source, logical_time_ns)

    async def latest_state_bind(
        prepared: object,
        logical_time_ns: int,
        source_sequence: int,
        visible_prefix: tuple[int, ...],
    ) -> object:
        with episode_scope(str(selected_cell["run_id"]), source_sequence):
            return await adapter.bind(
                prepared, logical_time_ns, source_sequence, visible_prefix
            )

    return S6BlockRuntime(
        semantic_prepare=semantic_prepare,
        latest_state_bind=latest_state_bind,
        production_core_identity_sha256=str(core["identity_sha256"]),
        work_volume_snapshot=lambda: snapshot_s6_work_volume(runtime),
        close=close,
    )


__all__ = [
    "S6BlockPreparationPaths",
    "S6ProductionError",
    "S6ProductionPaths",
    "build_s6_execution_identity",
    "build_s6_block_runtime",
    "collect_s6_read_only_observations",
    "current_s6_dependency_closure",
    "current_s6_source_closure",
    "instrument_s6_runtime",
    "load_s6_sources",
    "prepare_s6_block_authority",
    "snapshot_s6_work_volume",
]
