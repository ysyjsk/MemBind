from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.contracts import EpisodeInput
from saturated_fixed_work_baseline_v1_2.production_dependencies import (
    build_live_dependencies,
)


def _episode() -> EpisodeInput:
    return EpisodeInput(
        history_id="07741c45",
        session_id="session-0",
        source_sequence=0,
        source_hash="a" * 64,
        reference_time="2023/01/01 (Sun) 00:00",
        body="body",
        namespace="formal-namespace",
    )


@pytest.mark.asyncio
async def test_production_dependencies_bind_only_pinned_native_components(
    repository_root: Path, tmp_path: Path
) -> None:
    calls: list[tuple[str, object]] = []

    class Recorder:
        pass

    async def exporter(graphiti: Any, episodes: list[EpisodeInput], group_id: str) -> dict[str, object]:
        calls.append(("export", (graphiti, episodes, group_id)))
        return {"entities": [], "edges": [], "episodes": []}

    modules = {
        "native_characterization_tracing": SimpleNamespace(TraceRecorder=Recorder),
        "native_characterization_instrumentation": SimpleNamespace(
            install_native_characterization_instrumentation=lambda graph, recorder: (
                "phase",
                graph,
                recorder,
            )
        ),
        "native_characterization_c2_measurement": SimpleNamespace(
            install_c2_measurement_adapter=lambda graph, recorder: (
                "measurement",
                graph,
                recorder,
            )
        ),
        "live_outputs": SimpleNamespace(export_canonical_graph=exporter),
    }
    runtime = SimpleNamespace(graphiti=object())

    def runtime_builder(**kwargs: object) -> object:
        calls.append(("runtime", kwargs))
        return runtime

    message = object()
    dependencies = build_live_dependencies(
        repository_root=repository_root,
        service_idle=lambda: True,
        validation_loader=lambda root, name: modules[name],
        runtime_builder=runtime_builder,
        episode_source=message,
    )
    authority = tmp_path / "authority.json"
    authority.write_text("{}\n", encoding="utf-8")
    assert dependencies.runtime_factory("salt", authority) is runtime
    recorder = dependencies.recorder_factory()
    assert isinstance(recorder, Recorder)
    assert dependencies.instrumentation_installer("graph", recorder)[0] == "phase"
    assert dependencies.measurement_installer("graph", recorder)[0] == "measurement"
    exported = await dependencies.graph_exporter(runtime.graphiti, (_episode(),), "formal-namespace")
    assert exported["episodes"] == []
    assert dependencies.episode_source is message
    runtime_call = next(value for name, value in calls if name == "runtime")
    assert runtime_call == {
        "repository_root": repository_root,
        "cache_salt": "salt",
        "authority_path": authority,
    }


def test_episode_input_has_the_exact_graphiti_episode_name() -> None:
    assert _episode().name == "07741c45::episode::0000"


def test_production_dependencies_ast_has_no_forbidden_scheduler_or_oracle(
    repository_root: Path,
) -> None:
    path = (
        repository_root
        / "saturated_fixed_work_baseline_v1_2/src/saturated_fixed_work_baseline_v1_2/production_dependencies.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    source = ast.unparse(tree)
    for forbidden in (
        "membind_v5_oracle",
        "RequestAdmission",
        "apc_admission",
        "membind_scheduler",
    ):
        assert forbidden not in source
