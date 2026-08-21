"""Pinned production dependency composition for live block orchestration."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .contracts import EpisodeInput
from .live_block import LiveBlockDependencies
from .production_runtime import build_protocol_runtime
from .production_sampler import REQUIRED_PROBE_SOURCES
from .reuse import import_validation_module
from .sampler import PeriodicSampler


def build_neo4j_idle_probe(driver: Any) -> Callable[[], Any]:
    execute = getattr(driver, "execute_query", None)
    if not callable(execute):
        raise ValueError("NEO4J_IDLE_DRIVER_INVALID")

    async def probe() -> dict[str, Any]:
        result = execute(
            "SHOW TRANSACTIONS YIELD currentQuery "
            "WHERE currentQuery IS NULL OR NOT currentQuery STARTS WITH 'SHOW TRANSACTIONS' "
            "RETURN count(*) AS active_transactions",
            routing_="r",
        )
        if inspect.isawaitable(result):
            result = await result
        records = getattr(result, "records", result[0] if isinstance(result, tuple) else result)
        if not isinstance(records, Sequence) or len(records) != 1:
            raise ValueError("NEO4J_IDLE_RESULT_INVALID")
        record = records[0]
        try:
            active = int(
                (record if isinstance(record, Mapping) else dict(record))[
                    "active_transactions"
                ]
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("NEO4J_IDLE_RESULT_INVALID") from None
        if active < 0:
            raise ValueError("NEO4J_IDLE_RESULT_INVALID")
        return {"idle": active == 0, "active_transactions": active}

    return probe


def build_live_dependencies(
    *,
    repository_root: Path,
    service_idle: Callable[[], Any],
    validation_loader: Callable[[Path, str], Any] = import_validation_module,
    runtime_builder: Callable[..., Any] = build_protocol_runtime,
    episode_source: Any | None = None,
    sampler_probes: Mapping[str, Callable[[], Any]] | None = None,
) -> LiveBlockDependencies:
    tracing = validation_loader(
        repository_root, "native_characterization_tracing"
    )
    instrumentation = validation_loader(
        repository_root, "native_characterization_instrumentation"
    )
    measurement = validation_loader(
        repository_root, "native_characterization_c2_measurement"
    )
    live_outputs = validation_loader(repository_root, "live_outputs")
    recorder_type = getattr(tracing, "TraceRecorder", None)
    phase_installer = getattr(
        instrumentation, "install_native_characterization_instrumentation", None
    )
    measurement_installer = getattr(
        measurement, "install_c2_measurement_adapter", None
    )
    exporter = getattr(live_outputs, "export_canonical_graph", None)
    if not all(
        callable(value)
        for value in (
            recorder_type,
            phase_installer,
            measurement_installer,
            exporter,
            runtime_builder,
            service_idle,
        )
    ):
        raise ValueError("PINNED_LIVE_DEPENDENCY_MISSING")
    if episode_source is None:
        nodes = importlib.import_module("graphiti_core.nodes")
        episode_type = getattr(nodes, "EpisodeType", None)
        episode_source = getattr(episode_type, "message", None)
    if episode_source is None:
        raise ValueError("GRAPHITI_EPISODE_SOURCE_MISSING")
    sampler_factory = None
    if sampler_probes is not None:
        if set(sampler_probes) != set(REQUIRED_PROBE_SOURCES) or any(
            not callable(probe) for probe in sampler_probes.values()
        ):
            raise ValueError("PRODUCTION_SAMPLER_PROBES_INVALID")

        def sampler_factory(path: Path) -> PeriodicSampler:
            return PeriodicSampler(
                probes=sampler_probes,
                output_path=path,
                target_period_s=1.0,
            )

    def runtime_factory(cache_salt: str, authority_path: Path) -> Any:
        return runtime_builder(
            repository_root=repository_root,
            cache_salt=cache_salt,
            authority_path=authority_path,
        )

    async def graph_exporter(
        graphiti: Any,
        episodes: Sequence[EpisodeInput],
        namespace: str,
    ) -> Any:
        return await exporter(graphiti, list(episodes), namespace)

    return LiveBlockDependencies(
        runtime_factory=runtime_factory,
        graph_exporter=graph_exporter,
        recorder_factory=recorder_type,
        instrumentation_installer=phase_installer,
        measurement_installer=measurement_installer,
        episode_source=episode_source,
        service_idle=service_idle,
        sampler_factory=sampler_factory,
    )


__all__ = ["build_live_dependencies", "build_neo4j_idle_probe"]
