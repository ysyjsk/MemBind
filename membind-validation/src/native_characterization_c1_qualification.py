"""Run the frozen five-pair C1 A/A instrumentation qualification offline.

The fixture is intentionally Graphiti-shaped but contains no model, database,
network, or dataset input.  It qualifies wrapper transparency and estimates
local tracing perturbation; it is not a Native Graphiti performance result.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from native_characterization_instrumentation import (
    install_native_characterization_instrumentation,
)
from native_characterization_tracing import TraceRecorder


_PAIR_ORDERS = tuple(
    ("trace_off", "trace_on") if index % 2 == 0 else ("trace_on", "trace_off")
    for index in range(5)
)
_FORBIDDEN_FIELDS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "messages",
    "parameters",
    "prompt",
    "query",
    "raw_response",
    "response",
    "session_id",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _assert_sanitized(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_FIELDS:
                raise ValueError(f"forbidden qualification field: {key}")
            _assert_sanitized(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_sanitized(child)
        return
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise ValueError("qualification payload contains a non-JSON scalar")


def classify_overhead(overhead_ratios: Sequence[float]) -> str:
    """Classify the median of all five paired ratios using the frozen gate."""

    values = [float(value) for value in overhead_ratios]
    if len(values) != 5 or any(not math.isfinite(value) for value in values):
        raise ValueError("classification requires five finite paired ratios")
    median = sorted(values)[2]
    if median <= 0.02:
        return "clean_pass"
    if median <= 0.05:
        return "warning_continue"
    return "block_and_repair"


def build_qualification_result(
    pairs: Sequence[Mapping[str, Any]],
    *,
    state_sha256: str,
    event_sequence_sha256: str,
    source_hashes: Mapping[str, str],
) -> dict[str, Any]:
    """Normalize five timed pairs, compute the distribution, and seal it."""

    if len(pairs) != 5:
        raise ValueError("qualification requires exactly five A/A pairs")
    if any(key.casefold() in _FORBIDDEN_FIELDS for key in source_hashes):
        raise ValueError("forbidden qualification source field")

    normalized: list[dict[str, Any]] = []
    ratios: list[float] = []
    for index, raw in enumerate(pairs):
        order = list(raw.get("execution_order", []))
        if order != list(_PAIR_ORDERS[index]):
            raise ValueError("A/A pair order is not the frozen alternating order")
        off_ns = int(raw.get("trace_off_ns", 0))
        on_ns = int(raw.get("trace_on_ns", 0))
        if off_ns <= 0 or on_ns <= 0:
            raise ValueError("A/A durations must be positive")
        ratio = (on_ns - off_ns) / off_ns
        ratios.append(ratio)
        normalized.append(
            {
                "pair_index": index,
                "execution_order": order,
                "trace_off_ns": off_ns,
                "trace_on_ns": on_ns,
                "paired_overhead_ratio": ratio,
                "paired_overhead_percent": ratio * 100.0,
            }
        )

    median = sorted(ratios)[2]
    result: dict[str, Any] = {
        "schema_version": "membind.native-characterization-c1-qualification.v1",
        "artifact_id": "native-characterization-c1-aa-qualification",
        "run_id": "native-characterization-c1-offline-fixture",
        "creation_command": (
            ".venv/bin/python src/native_characterization_c1_qualification.py"
        ),
        "fixture_scope": "deterministic_graphiti_shaped_offline_not_c2_workload",
        "pair_count": 5,
        "pair_order_policy": "alternate_off_on_then_on_off",
        "classification_statistic": "median_paired_overhead_ratio",
        "guardrail": {
            "clean_pass_max_ratio": 0.02,
            "warning_continue_max_ratio": 0.05,
            "above_warning_action": "block_and_repair",
        },
        "pairs": normalized,
        "paired_distribution": {
            "overhead_ratio": ratios,
            "minimum_ratio": min(ratios),
            "median_ratio": median,
            "maximum_ratio": max(ratios),
        },
        "classification": classify_overhead(ratios),
        "semantic_parity": {
            "passed": True,
            "state_sha256": str(state_sha256),
            "event_sequence_sha256": str(event_sequence_sha256),
        },
        "source_hashes": dict(source_hashes),
    }
    _assert_sanitized(result)
    result["payload_sha256"] = _sha256_bytes(canonical_bytes(result))
    return result


def validate_result(result: Mapping[str, Any]) -> None:
    if not isinstance(result, Mapping):
        raise ValueError("qualification result must be an object")
    candidate = deepcopy(dict(result))
    observed_hash = candidate.pop("payload_sha256", None)
    _assert_sanitized(candidate)
    if observed_hash != _sha256_bytes(canonical_bytes(candidate)):
        raise ValueError("payload_sha256 mismatch")
    if candidate.get("schema_version") != (
        "membind.native-characterization-c1-qualification.v1"
    ):
        raise ValueError("qualification schema mismatch")
    pairs = candidate.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != 5:
        raise ValueError("qualification requires five pairs")
    ratios: list[float] = []
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict) or pair.get("execution_order") != list(
            _PAIR_ORDERS[index]
        ):
            raise ValueError("qualification pair order mismatch")
        off_ns = int(pair.get("trace_off_ns", 0))
        on_ns = int(pair.get("trace_on_ns", 0))
        expected_ratio = (on_ns - off_ns) / off_ns
        if not math.isclose(
            float(pair.get("paired_overhead_ratio")),
            expected_ratio,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("qualification paired ratio mismatch")
        ratios.append(expected_ratio)
    if candidate.get("classification") != classify_overhead(ratios):
        raise ValueError("qualification classification mismatch")


def _cpu_work(label: str, work_units: int) -> str:
    digest = hashlib.sha256(label.encode("ascii")).digest()
    for _ in range(work_units):
        digest = hashlib.sha256(digest).digest()
    return digest.hex()


class _OfflineFixture:
    """Small deterministic call graph covering every high-level wrapper."""

    def __init__(self, work_units: int) -> None:
        self.work_units = work_units
        self.events: list[str] = []
        self.graph_state: list[str] = []

        def phase(name: str):
            async def call(*_args: Any, **_kwargs: Any) -> str:
                self.events.append(name)
                return _cpu_work(name, self.work_units)

            return call

        phase_names = (
            "extract_nodes",
            "resolve_extracted_nodes",
            "extract_edges",
            "resolve_extracted_edges",
            "extract_attributes_from_nodes",
        )
        self.phase_module = SimpleNamespace(
            **{name: phase(name) for name in phase_names}
        )

        async def transport_create(*_args: Any, **_kwargs: Any) -> Any:
            self.events.append("llm-transport")
            _cpu_work("llm-transport", self.work_units)
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2)
            )

        async def generate_response(*_args: Any, **_kwargs: Any) -> str:
            self.events.append("llm")
            await llm.client.chat.completions.create(request_kind="fixture")
            return _cpu_work("llm", self.work_units)

        llm = SimpleNamespace(
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=transport_create)
                )
            ),
            generate_response=generate_response,
        )

        async def create_embedding(*_args: Any, **_kwargs: Any) -> list[float]:
            self.events.append("embedding")
            digest = _cpu_work("embedding", self.work_units)
            return [float(int(digest[:2], 16)), float(int(digest[2:4], 16))]

        async def execute_query(*_args: Any, **_kwargs: Any) -> str:
            self.events.append("database")
            return _cpu_work("database", self.work_units)

        async def retrieve_episodes(*_args: Any, **_kwargs: Any) -> str:
            self.events.append("retrieve_episodes")
            return _cpu_work("retrieve_episodes", self.work_units)

        async def publish(*_args: Any, **_kwargs: Any) -> str:
            self.events.append("publication")
            return _cpu_work("publication", self.work_units)

        async def add_episode(payload: str, *, option: int) -> str:
            self.events.append("add_episode")
            values = [await graphiti.retrieve_episodes(payload)]
            for name in phase_names:
                values.append(await getattr(self.phase_module, name)(values[-1]))
            values.append(
                await graphiti.llm_client.generate_response(
                    payload,
                    prompt_name="fixture.operation",
                )
            )
            values.append(await graphiti.embedder.create(input_data=payload))
            values.append(await graphiti.driver.execute_query(payload, routing_="r"))
            values.append(await graphiti._process_episode_data(values[-1]))
            state = _sha256_bytes(canonical_bytes([option, *values]))
            self.graph_state.append(state)
            return state

        graphiti = SimpleNamespace(
            llm_client=llm,
            embedder=SimpleNamespace(create=create_embedding),
            driver=SimpleNamespace(execute_query=execute_query),
            retrieve_episodes=retrieve_episodes,
            _process_episode_data=publish,
            add_episode=add_episode,
        )
        self.graphiti = graphiti


async def _timed_fixture_run(
    *, trace_on: bool, work_units: int
) -> tuple[int, str, str]:
    fixture = _OfflineFixture(work_units)
    recorder = TraceRecorder()
    installation = None
    if trace_on:
        installation = install_native_characterization_instrumentation(
            fixture.graphiti,
            recorder,
            phase_module=fixture.phase_module,
        )
    try:
        start_ns = time.perf_counter_ns()
        if trace_on:
            with recorder.episode_scope("c1-aa", "fixture", 0):
                result = await fixture.graphiti.add_episode("fixture", option=7)
        else:
            result = await fixture.graphiti.add_episode("fixture", option=7)
        end_ns = time.perf_counter_ns()
    finally:
        if installation is not None:
            installation.restore()
    state_sha = _sha256_bytes(canonical_bytes([result, *fixture.graph_state]))
    event_sha = _sha256_bytes(canonical_bytes(fixture.events))
    return end_ns - start_ns, state_sha, event_sha


async def run_qualification(*, work_units: int = 20_000) -> dict[str, Any]:
    """Execute exactly five fresh alternating pairs and verify parity."""

    if not isinstance(work_units, int) or work_units <= 0:
        raise ValueError("work_units must be a positive integer")
    pairs: list[dict[str, Any]] = []
    state_hashes: set[str] = set()
    event_hashes: set[str] = set()
    for pair_index, order in enumerate(_PAIR_ORDERS):
        durations: dict[str, int] = {}
        for mode in order:
            duration, state_sha, event_sha = await _timed_fixture_run(
                trace_on=mode == "trace_on",
                work_units=work_units,
            )
            durations[mode] = duration
            state_hashes.add(state_sha)
            event_hashes.add(event_sha)
        pairs.append(
            {
                "pair_index": pair_index,
                "execution_order": list(order),
                "trace_off_ns": durations["trace_off"],
                "trace_on_ns": durations["trace_on"],
            }
        )
    if len(state_hashes) != 1 or len(event_hashes) != 1:
        raise ValueError("trace-off/on semantic parity failed")

    module_path = Path(__file__).resolve()
    instrumentation_path = module_path.parent / "native_characterization_instrumentation.py"
    return build_qualification_result(
        pairs,
        state_sha256=next(iter(state_hashes)),
        event_sequence_sha256=next(iter(event_hashes)),
        source_hashes={
            "qualification_source_sha256": _sha256_file(module_path),
            "instrumentation_source_sha256": _sha256_file(instrumentation_path),
        },
    )


def _atomic_write(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_result(result: Mapping[str, Any], path: str | Path) -> str:
    validate_result(result)
    encoded = canonical_bytes(result) + b"\n"
    _atomic_write(Path(path), encoded)
    return _sha256_bytes(encoded)


def _main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-units", type=int, default=20_000)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            root
            / "artifacts/tdd/native_characterization_c1_aa_qualification_20260810.json"
        ),
    )
    args = parser.parse_args()
    result = asyncio.run(run_qualification(work_units=args.work_units))
    artifact_sha256 = write_result(result, args.output)
    print(
        json.dumps(
            {
                "artifact_sha256": artifact_sha256,
                "classification": result["classification"],
                "median_overhead_percent": (
                    result["paired_distribution"]["median_ratio"] * 100.0
                ),
                "output": str(args.output),
                "pair_count": result["pair_count"],
                "semantic_parity": result["semantic_parity"]["passed"],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 2 if result["classification"] == "block_and_repair" else 0


if __name__ == "__main__":
    raise SystemExit(_main())
