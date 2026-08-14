"""Production-safe runtime primitives for the read-only S2-R0 probe."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import atomic_write_json, finalize_envelope
from .s2_retrieval_probe import ProbeCounters


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_NEO4J_URI = "bolt://localhost:7687"


@dataclass(frozen=True)
class S2R0RuntimeComponents:
    driver_type: Any
    graphiti_type: Any
    llm_factory: Callable[[ProbeCounters], Any]
    embedder_factory: Callable[[ProbeCounters], Any]
    cross_encoder_factory: Callable[[ProbeCounters], Any]


@dataclass(frozen=True)
class S2R0Runtime:
    graphiti: Any
    counters: ProbeCounters
    telemetry_enabled: bool = False


def _production_components() -> S2R0RuntimeComponents:
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.client import CrossEncoderClient
    from graphiti_core.driver.neo4j_driver import Neo4jDriver
    from graphiti_core.embedder.client import EmbedderClient
    from graphiti_core.llm_client.client import LLMClient
    from graphiti_core.llm_client.config import LLMConfig

    class ForbiddenLLM(LLMClient):
        def __init__(self, counters: ProbeCounters) -> None:
            self._counters = counters
            super().__init__(
                LLMConfig(
                    api_key="s2-r0-forbidden",
                    model="s2-r0-forbidden",
                    small_model="s2-r0-forbidden",
                    max_tokens=1,
                )
            )

        async def _generate_response(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self._counters.construction_llm_requests += 1
            raise RuntimeError("S2-R0 forbids construction LLM requests")

    class ForbiddenEmbedder(EmbedderClient):
        def __init__(self, counters: ProbeCounters) -> None:
            self._counters = counters

        async def create(self, input_data: Any) -> list[float]:
            self._counters.embedding_requests += 1
            raise RuntimeError("S2-R0 forbids embedding requests")

    class ForbiddenCrossEncoder(CrossEncoderClient):
        def __init__(self, counters: ProbeCounters) -> None:
            self._counters = counters

        async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
            self._counters.cross_encoder_requests += 1
            raise RuntimeError("S2-R0 forbids cross-encoder requests")

    return S2R0RuntimeComponents(
        driver_type=Neo4jDriver,
        graphiti_type=Graphiti,
        llm_factory=ForbiddenLLM,
        embedder_factory=ForbiddenEmbedder,
        cross_encoder_factory=ForbiddenCrossEncoder,
    )


def build_read_only_graphiti(
    *,
    env: Mapping[str, str],
    components: S2R0RuntimeComponents | None = None,
) -> S2R0Runtime:
    """Build Neo4jDriver outside an event loop so schema init is never scheduled."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("S2-R0 runtime must be built outside an active event loop")
    uri = env.get("NEO4J_URI")
    user = env.get("NEO4J_USER")
    password = env.get("NEO4J_PASSWORD")
    if uri != EXPECTED_NEO4J_URI or not user or not password:
        raise ValueError("S2-R0 local Neo4j identity is incomplete or drifted")
    os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
    selected = components or _production_components()
    counters = ProbeCounters()
    driver = selected.driver_type(uri, user, password)
    if getattr(driver, "_init_task", None) is not None:
        raise RuntimeError("S2-R0 Neo4j driver scheduled schema initialization")
    graphiti = selected.graphiti_type(
        graph_driver=driver,
        llm_client=selected.llm_factory(counters),
        embedder=selected.embedder_factory(counters),
        cross_encoder=selected.cross_encoder_factory(counters),
    )
    if getattr(graphiti, "driver", None) is not driver:
        raise RuntimeError("S2-R0 Graphiti driver identity drift")
    return S2R0Runtime(graphiti=graphiti, counters=counters)


def finalize_s2r0_failure(
    output_path: Path,
    *,
    run_id: str,
    history_id: str,
    namespace: str,
    error: BaseException,
    counters: ProbeCounters,
    authorization_sha256: str,
    consumption_sha256: str,
    git_commit: str,
) -> dict[str, Any]:
    """Persist only a stable failure class and observed counters."""

    path = Path(output_path)
    if path.exists():
        raise ValueError("S2-R0 failure artifact already exists")
    if _SHA256.fullmatch(authorization_sha256) is None:
        raise ValueError("S2-R0 authorization hash is invalid")
    if _SHA256.fullmatch(consumption_sha256) is None:
        raise ValueError("S2-R0 consumption hash is invalid")
    payload = {
        "schema_version": "membind.paper-eval-v3.s2-r0-failure.v1",
        "stage": "S2-R0",
        "status": "FAILED_STOPPED",
        "run_id": run_id,
        "history_id": history_id,
        "namespace": namespace,
        "error_class": type(error).__name__,
        "result_mergeable": False,
        "retrieval_conclusion": "NOT_PRODUCED",
        "s3_authorized": False,
        "authorization_sha256": authorization_sha256,
        "consumption_sha256": consumption_sha256,
        **counters.snapshot().to_artifact(),
    }
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=run_id,
    )
    atomic_write_json(path, artifact)
    return artifact
