"""Executable GPT-5.4-mini + local BGE-M3 bounded characterization.

The historical parent directory keeps its old name for isolation compatibility.
This module is the active bounded sub-lane.  It never imports into mainline
``src/**`` and does not read credentials, dataset content, GPU state, or Neo4j
until the immutable Chat preflight gate has passed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import MethodType
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlsplit

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from gpt55_temporary.api_characterization.bounded_runner import (
    DEFAULT_EMBEDDING_CACHE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REVISION,
    DEFAULT_MODEL,
    FROZEN_EPISODE_SHA256,
    FROZEN_HISTORY_ID,
    FROZEN_SOURCE_SEQUENCE,
    BoundedRunConfig,
    run_bounded,
)
from gpt55_temporary.api_characterization.live_experiment import (
    LiveExperimentConfig,
    run_live_experiment,
)
from gpt55_temporary.api_characterization.live_runtime import (
    AsyncOpenAIChatTransport,
    BoundedGraphitiLLMClient,
    C1TraceInstrumentor,
    load_frozen_episode,
)
from gpt55_temporary.simple_judge.config_chat_judge import (
    DEFAULT_CONFIG,
    RelayConfig,
    _atomic_write_json,
    chat_completions_url,
    load_relay_config,
)


LANE_ROOT = Path(__file__).resolve().parents[1]
VALIDATION_ROOT = LANE_ROOT.parent
MAINLINE_SRC = VALIDATION_ROOT / "src"
DEFAULT_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
    "longmemeval_s_cleaned.json"
)
DEFAULT_LIVE_ARTIFACT_ROOT = LANE_ROOT / "artifacts" / "api_characterization"
DEFAULT_NEO4J_URI = "bolt://localhost:7687"
DEFAULT_NEO4J_DATABASE = "neo4j"
EXPECTED_GPU_NAME_FRAGMENT = "RTX 3090 Ti"
STRUCTURED_PREFLIGHT_PROMPT = (
    "Return one JSON object with compatible=true. Do not include other text."
)
_SCOPED_CLEANUP_QUERY = """
MATCH (node)
WHERE node.group_id = $group_id
DETACH DELETE node
""".strip()
_ATTEMPT_NAMESPACE_COUNT_QUERY = """
MATCH (node)
WHERE node.group_id = $group_id
RETURN count(node) AS node_count
""".strip()


class StructuredCompatibility(BaseModel):
    compatible: bool


class UnplannedCrossEncoderCall(RuntimeError):
    """The bounded construction path unexpectedly requested reranking."""


class AttemptNamespaceNotEmptyError(RuntimeError):
    """The bounded attempt namespace contains pre-existing graph state."""


class ForbiddenConstructionCrossEncoder(CrossEncoderClient):
    """Fail closed instead of adding a second adapter-authored prompt."""

    def __init__(self) -> None:
        self.rank_call_count = 0

    async def rank(
        self, query: str, passages: list[str]
    ) -> list[tuple[str, float]]:
        self.rank_call_count += 1
        raise UnplannedCrossEncoderCall(
            "bounded construction invoked the forbidden cross encoder"
        )


@dataclass(frozen=True)
class ProductionEpisode:
    """The exact Graphiti arguments for one source-bound episode."""

    question_id: str
    source_sequence: int
    source_hash: str
    name: str
    episode_body: str = field(repr=False)
    source_description: str
    reference_time: Any
    source: Any


@dataclass(frozen=True)
class Neo4jCredentials:
    uri: str
    user: str
    password: str = field(repr=False)
    database: str = DEFAULT_NEO4J_DATABASE


@dataclass(frozen=True)
class ProductionContext:
    relay: RelayConfig
    episode: ProductionEpisode
    neo4j: Neo4jCredentials


@dataclass(frozen=True)
class ProductionRunConfig:
    """Frozen live inputs; secrets remain in external config and ``.env``."""

    attempt_id: str
    preflight_attempt_dir: Path
    artifact_root: Path = DEFAULT_LIVE_ARTIFACT_ROOT
    codex_config_path: Path = DEFAULT_CONFIG
    dataset_path: Path = DEFAULT_DATASET
    env_path: Path = VALIDATION_ROOT / ".env"
    model: str = DEFAULT_MODEL
    timeout_s: float = 180.0
    max_api_attempts: int = 64
    max_tokens: int = 4096
    max_coroutines: int = 4

    def __post_init__(self) -> None:
        if self.model != DEFAULT_MODEL:
            raise ValueError("the active bounded model is frozen to gpt-5.4-mini")
        for name in (
            "preflight_attempt_dir",
            "artifact_root",
            "codex_config_path",
            "dataset_path",
            "env_path",
        ):
            object.__setattr__(self, name, Path(getattr(self, name)))


def _ensure_mainline_import_path() -> None:
    if str(MAINLINE_SRC) not in sys.path:
        sys.path.insert(0, str(MAINLINE_SRC))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def run_structured_chat_preflight(
    *,
    relay_config: RelayConfig,
    run_dir: str | Path,
    transport_factory: Callable[[RelayConfig], Any] | None = None,
) -> dict[str, Any]:
    """Issue one zero-retry JSON-schema request and persist no content."""

    endpoint = chat_completions_url(relay_config.base_url)
    factory = transport_factory or (
        lambda relay: AsyncOpenAIChatTransport(
            endpoint=chat_completions_url(relay.base_url),
            api_key=relay.api_key,
            timeout_s=relay.timeout_s,
        )
    )
    transport: Any | None = None
    client: BoundedGraphitiLLMClient | None = None
    started_ns = time.monotonic_ns()
    status_code: int | None = None
    error_code: str | None = None
    classification = "structured_chat_compatible"
    ok = False
    close_error: BaseException | None = None
    try:
        transport = factory(relay_config)
        client = BoundedGraphitiLLMClient(
            endpoint=endpoint,
            api_key=relay_config.api_key,
            model=relay_config.model,
            transport=transport,
            max_api_attempts=1,
            max_tokens=64,
        )
        response = await client.generate_response(
            [Message(role="user", content=STRUCTURED_PREFLIGHT_PROMPT)],
            response_model=StructuredCompatibility,
            max_tokens=64,
            prompt_name="temporary.structured_compatibility",
        )
        if response.get("compatible") is not True:
            raise ValueError("structured compatibility response was negative")
        status_code = 200
        ok = True
    except BaseException as exc:
        status_code = _status_code(exc)
        error_code = f"{type(exc).__module__}.{type(exc).__qualname__}"
        classification = (
            f"http_{status_code}"
            if status_code is not None
            else "structured_chat_transport_or_protocol_failure"
        )
    finally:
        if transport is not None:
            close = getattr(transport, "close", None)
            if callable(close):
                try:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
                except BaseException as exc:
                    close_error = exc
        if close_error is not None and ok:
            ok = False
            status_code = None
            classification = "structured_chat_transport_close_failure"
            error_code = (
                f"{type(close_error).__module__}.{type(close_error).__qualname__}"
            )

    report = {
        "schema_version": "membind.temporary-structured-chat-preflight.v1",
        "ok": ok,
        "status": "success" if ok else "failed",
        "status_code": status_code,
        "classification": classification,
        "error_code": error_code,
        "model": relay_config.model,
        "provider_name": relay_config.provider_name,
        "config_declared_wire_api": relay_config.config_declared_wire_api,
        "effective_wire_api": "chat",
        "endpoint": endpoint,
        "attempt_count": int(client.attempt_count if client is not None else 0),
        "max_retries": 0,
        "client_observed_latency_ms": (time.monotonic_ns() - started_ns) / 1_000_000,
        "latency_semantics": "caller_observed_api_wait_not_model_execution_time",
        "request_message_roles": ["user"],
        "request_message_count": 1,
        "prompt_sha256": _sha256_text(STRUCTURED_PREFLIGHT_PROMPT),
        "prompt_utf8_bytes": len(STRUCTURED_PREFLIGHT_PROMPT.encode("utf-8")),
        "client_injected_system_prompt": False,
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "diagnostic_only": True,
        "mainline_state_advanced": False,
    }
    _atomic_write_json(Path(run_dir) / "01_structured_chat_preflight.json", report)
    return report


def load_production_episode(
    *,
    dataset_path: str | Path,
    records_loader: Callable[[str | Path], list[dict[str, Any]]],
    episode_builder: Callable[[dict[str, Any]], list[Any]],
    reference_time_parser: Callable[[str], Any],
    episode_source: Any,
) -> ProductionEpisode:
    """Load and convert exactly the frozen source without persisting content."""

    source = load_frozen_episode(
        dataset_path=dataset_path,
        history_id=FROZEN_HISTORY_ID,
        source_sequence=FROZEN_SOURCE_SEQUENCE,
        expected_sha256=FROZEN_EPISODE_SHA256,
        records_loader=records_loader,
        episode_builder=episode_builder,
    )
    return ProductionEpisode(
        question_id=str(source.question_id),
        source_sequence=int(source.source_sequence),
        source_hash=str(source.source_hash),
        name=str(source.name),
        episode_body=str(source.body),
        source_description="LongMemEval-S haystack session",
        reference_time=reference_time_parser(str(source.reference_time)),
        source=episode_source,
    )


def validate_local_neo4j_uri(value: str) -> str:
    """Accept only a local Bolt/Neo4j endpoint for this temporary run."""

    parsed = urlsplit(str(value))
    if parsed.scheme not in {"bolt", "neo4j"}:
        raise ValueError("temporary characterization requires a Bolt Neo4j URI")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("temporary characterization requires local Neo4j")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Neo4j credentials must not be embedded in the URI")
    return str(value)


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name:
            values[name] = value.strip().strip("\"'")
    return values


def load_neo4j_credentials(path: str | Path) -> Neo4jCredentials:
    values = _read_dotenv(Path(path))

    def selected(name: str, default: str | None = None) -> str:
        value = str(os.environ.get(name) or values.get(name) or default or "")
        if not value:
            raise ValueError(f"temporary runtime is missing {name}")
        return value

    return Neo4jCredentials(
        uri=validate_local_neo4j_uri(selected("NEO4J_URI", DEFAULT_NEO4J_URI)),
        user=selected("NEO4J_USER"),
        password=selected("NEO4J_PASSWORD"),
        database=selected("NEO4J_DATABASE", DEFAULT_NEO4J_DATABASE),
    )


async def cleanup_attempt_group(
    *,
    driver: Any,
    group_id: str,
    expected_group_id: str,
) -> None:
    """Delete only nodes owned by the exact fresh temporary namespace."""

    if group_id != expected_group_id or not group_id.startswith("tmp-api-char-"):
        raise ValueError("refusing cleanup outside the bounded attempt namespace")
    await driver.execute_query(
        _SCOPED_CLEANUP_QUERY,
        params={"group_id": group_id},
    )


async def assert_attempt_group_empty(
    *,
    driver: Any,
    group_id: str,
    expected_group_id: str,
) -> None:
    """Fail closed unless the exact bounded namespace has no existing nodes."""

    if group_id != expected_group_id or not group_id.startswith("tmp-api-char-"):
        raise ValueError("refusing freshness check outside the bounded namespace")
    result = await driver.execute_query(
        _ATTEMPT_NAMESPACE_COUNT_QUERY,
        params={"group_id": group_id},
    )
    records = getattr(result, "records", None)
    if records is None and isinstance(result, tuple) and result:
        records = result[0]
    if records is None and isinstance(result, list):
        records = result
    if not isinstance(records, (list, tuple)) or len(records) != 1:
        raise RuntimeError("namespace count query did not return exactly one record")
    try:
        node_count = records[0]["node_count"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("namespace count query did not return node_count") from exc
    if (
        not isinstance(node_count, int)
        or isinstance(node_count, bool)
        or node_count < 0
    ):
        raise RuntimeError(
            f"namespace count query returned invalid value: {node_count!r}"
        )
    if node_count != 0:
        raise AttemptNamespaceNotEmptyError(
            f"bounded attempt namespace contains {node_count} existing nodes"
        )


def _validate_preflight_binding(
    attempt_dir: Path,
    relay: RelayConfig,
) -> dict[str, Any]:
    try:
        manifest = json.loads(
            (attempt_dir / "00_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "ok": False,
            "status_code": None,
            "classification": "preflight_manifest_unreadable",
            "attempt_count": 0,
        }
    exact = (
        manifest.get("model") == relay.model
        and manifest.get("provider_name") == relay.provider_name
        and manifest.get("endpoint") == chat_completions_url(relay.base_url)
        and manifest.get("effective_wire_api") == "chat"
    )
    return {
        "ok": bool(exact),
        "status_code": None,
        "classification": (
            "preflight_binding_match" if exact else "preflight_binding_mismatch"
        ),
        "attempt_count": 0,
    }


def _default_episode_loader(dataset_path: Path) -> ProductionEpisode:
    _ensure_mainline_import_path()
    from dataset import build_episodes, load_json_records
    from graphiti_core.nodes import EpisodeType
    from graphiti_native import parse_datetime

    return load_production_episode(
        dataset_path=dataset_path,
        records_loader=load_json_records,
        episode_builder=build_episodes,
        reference_time_parser=parse_datetime,
        episode_source=EpisodeType.message,
    )


def _default_embedding_factory(config: ProductionRunConfig) -> Any:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("local CUDA is unavailable for BGE-M3")
    device_name = str(torch.cuda.get_device_name(0))
    if EXPECTED_GPU_NAME_FRAGMENT not in device_name:
        raise RuntimeError("CUDA device 0 is not the frozen RTX 3090 Ti")
    scripts = LANE_ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from local_embedding_adapter import LocalBgeM3Embedder

    embedder = LocalBgeM3Embedder(
        model=DEFAULT_EMBEDDING_MODEL,
        revision=DEFAULT_EMBEDDING_REVISION,
        cache_folder=DEFAULT_EMBEDDING_CACHE,
        dimension=1024,
        batch_size=32,
    )
    # Synthetic warmup loads the already-local model without touching the
    # experimental episode or a persistent cache.
    embedder.create_batch_sync(["temporary embedding warmup"])
    return embedder


async def _default_neo4j_factory(credentials: Neo4jCredentials) -> Any:
    from graphiti_core.driver.neo4j_driver import Neo4jDriver

    driver = Neo4jDriver(
        credentials.uri,
        credentials.user,
        credentials.password,
        database=credentials.database,
    )
    try:
        init_task = getattr(driver, "_init_task", None)
        if init_task is not None:
            await init_task
        else:
            await driver.build_indices_and_constraints()
        return driver
    except BaseException:
        await driver.close()
        raise


async def _build_graphiti(
    *,
    context: ProductionContext,
    embedding: Any,
    neo4j: Any,
    config: ProductionRunConfig,
) -> Any:
    from graphiti_core import Graphiti

    endpoint = chat_completions_url(context.relay.base_url)
    transport: AsyncOpenAIChatTransport | None = None
    try:
        transport = AsyncOpenAIChatTransport(
            endpoint=endpoint,
            api_key=context.relay.api_key,
            timeout_s=config.timeout_s,
        )
        llm = BoundedGraphitiLLMClient(
            endpoint=endpoint,
            api_key=context.relay.api_key,
            model=config.model,
            transport=transport,
            max_api_attempts=config.max_api_attempts,
            max_tokens=config.max_tokens,
        )
        graphiti = Graphiti(
            graph_driver=neo4j,
            llm_client=llm,
            embedder=embedding,
            cross_encoder=ForbiddenConstructionCrossEncoder(),
            max_coroutines=config.max_coroutines,
            store_raw_episode_content=False,
        )
    except BaseException:
        if transport is not None:
            await transport.close()
        # The live orchestrator still owns Neo4j until this factory returns.
        raise

    original_close = graphiti.close

    async def close_owned(_self: Any) -> None:
        errors: list[BaseException] = []
        try:
            await original_close()
        except BaseException as exc:
            errors.append(exc)
        try:
            await transport.close()
        except BaseException as exc:
            errors.append(exc)
        if errors:
            raise errors[0]

    graphiti.close = MethodType(close_owned, graphiti)
    return graphiti


async def execute_production_run(config: ProductionRunConfig) -> dict[str, Any]:
    """Gate and run one fresh, isolated episode with production dependencies."""

    bounded_config = BoundedRunConfig(
        attempt_id=config.attempt_id,
        artifact_root=config.artifact_root / config.attempt_id / "bounded",
        model=config.model,
        max_tokens=config.max_tokens,
        max_coroutines=config.max_coroutines,
        timeout_s=config.timeout_s,
        max_api_attempts=config.max_api_attempts,
    )
    holder: dict[str, Any] = {}

    async def compatibility_preflight(*, run_dir: Path) -> dict[str, Any]:
        relay = load_relay_config(
            config.codex_config_path,
            model=config.model,
            timeout_s=config.timeout_s,
            allow_config_wire_override=True,
        )
        binding = _validate_preflight_binding(config.preflight_attempt_dir, relay)
        if not binding["ok"]:
            return binding
        report = await run_structured_chat_preflight(
            relay_config=relay,
            run_dir=run_dir,
        )
        if report["ok"]:
            holder["relay"] = relay
        return report

    def dataset_loader() -> ProductionContext:
        relay = holder.get("relay")
        if not isinstance(relay, RelayConfig):
            raise RuntimeError("structured preflight did not bind relay config")
        context = ProductionContext(
            relay=relay,
            episode=_default_episode_loader(config.dataset_path),
            neo4j=load_neo4j_credentials(config.env_path),
        )
        holder["context"] = context
        return context

    def embedding_factory() -> Any:
        return _default_embedding_factory(config)

    async def neo4j_factory() -> Any:
        context = holder.get("context")
        if not isinstance(context, ProductionContext):
            raise RuntimeError("production context is unavailable")
        return await _default_neo4j_factory(context.neo4j)

    async def graphiti_factory(*, dataset: Any, embedding: Any, neo4j: Any) -> Any:
        if not isinstance(dataset, ProductionContext):
            raise TypeError("production dataset context is invalid")
        return await _build_graphiti(
            context=dataset,
            embedding=embedding,
            neo4j=neo4j,
            config=config,
        )

    async def experiment_runner(
        *,
        graphiti: Any,
        dataset: Any,
        run_dir: Path,
        resource_handoff: Any,
    ) -> Any:
        if not isinstance(dataset, ProductionContext):
            raise TypeError("production dataset context is invalid")
        await assert_attempt_group_empty(
            driver=graphiti.driver,
            group_id=bounded_config.graph_namespace,
            expected_group_id=bounded_config.graph_namespace,
        )
        bounded_run_dir = bounded_config.artifact_root / bounded_config.attempt_id
        instrumentor = C1TraceInstrumentor(
            run_id=config.attempt_id,
            episode_id=dataset.episode.question_id,
            source_sequence=dataset.episode.source_sequence,
            run_dir=bounded_run_dir,
        )

        async def cleanup_group(*, group_id: str) -> None:
            await cleanup_attempt_group(
                driver=graphiti.driver,
                group_id=group_id,
                expected_group_id=bounded_config.graph_namespace,
            )

        return await run_bounded(
            config=bounded_config,
            episode_loader=lambda **_kwargs: dataset.episode,
            graphiti_factory=lambda _bounded: graphiti,
            instrumentor=instrumentor,
            cleanup_group=cleanup_group,
            resource_handoff=resource_handoff,
        )

    return await run_live_experiment(
        config=LiveExperimentConfig(
            attempt_id=config.attempt_id,
            preflight_attempt_dir=config.preflight_attempt_dir,
            artifact_root=config.artifact_root,
            expected_model=config.model,
        ),
        compatibility_preflight=compatibility_preflight,
        dataset_loader=dataset_loader,
        embedding_factory=embedding_factory,
        neo4j_factory=neo4j_factory,
        graphiti_factory=graphiti_factory,
        experiment_runner=experiment_runner,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--preflight-attempt-dir", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_LIVE_ARTIFACT_ROOT)
    parser.add_argument("--codex-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--env-file", type=Path, default=VALIDATION_ROOT / ".env")
    parser.add_argument("--timeout-s", type=float, default=180.0)
    parser.add_argument("--max-api-attempts", type=int, default=64)
    return parser


def main() -> int:
    args = _parser().parse_args()
    config = ProductionRunConfig(
        attempt_id=args.attempt_id,
        preflight_attempt_dir=args.preflight_attempt_dir,
        artifact_root=args.artifact_root,
        codex_config_path=args.codex_config,
        dataset_path=args.dataset,
        env_path=args.env_file,
        timeout_s=args.timeout_s,
        max_api_attempts=args.max_api_attempts,
    )
    try:
        result = asyncio.run(execute_production_run(config))
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "diagnostic_only": True,
                    "mainline_state_advanced": False,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result.get("status") == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
