"""Prepare and execute the single bounded Native Graphiti C0 episode.

Dry-run preparation is local and content-safe.  The live path checks the exact
C0 action before reading episode content, loading `.env`, creating clients, or
writing a run directory.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import tempfile
import time
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from current_state_gate import LiveAction, require_live_action
from dataset import Episode, build_episodes
from native_characterization_freeze import validate_artifact
from native_characterization_runtime import build_u0_graphiti_from_env


_RUN_ID_RE = re.compile(r"c0-[0-9a-f]{16}")
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


def _canonical_bytes(value: Any) -> bytes:
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
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_sanitized(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_FIELDS:
                raise ValueError(f"forbidden C0 artifact field: {key}")
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
    raise ValueError("C0 artifact contains a non-JSON scalar")


@dataclass(frozen=True)
class C0Invocation:
    episode: Episode
    graph_namespace: str
    freeze_payload_sha256: str
    freeze_file_sha256: str

    @property
    def run_id(self) -> str:
        return f"c0-{self.graph_namespace.rsplit('-', 1)[-1]}"

    def to_preview(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.native-characterization-c0-preview.v1",
            "run_id": self.run_id,
            "history_id": self.episode.question_id,
            "source_sequence": self.episode.source_sequence,
            "episode_source_sha256": self.episode.source_hash,
            "graph_namespace": self.graph_namespace,
            "freeze_payload_sha256": self.freeze_payload_sha256,
            "freeze_file_sha256": self.freeze_file_sha256,
            "live_request_performed": False,
        }


def prepare_c0_invocation(
    *,
    validation_root: str | Path,
    source_path: str | Path,
) -> C0Invocation:
    """Resolve exactly the frozen C0 episode without retaining it in output."""

    validation = Path(validation_root).resolve()
    source = Path(source_path).resolve()
    freeze_path = validation / "artifacts/native_characterization/freeze.json"
    try:
        freeze = json.loads(freeze_path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("C0 freeze is unreadable") from None
    validate_artifact(freeze)
    if _sha256_file(source) != freeze.get("dataset", {}).get("source_sha256"):
        raise ValueError("C0 source identity mismatch")

    c0 = freeze.get("screening", {}).get("c0")
    if not isinstance(c0, dict):
        raise ValueError("C0 selection missing from freeze")
    history_id = str(c0.get("history_id", ""))
    source_sequence = c0.get("source_sequence")
    try:
        records = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("C0 source is unreadable") from None
    if not isinstance(records, list):
        raise ValueError("C0 source must be a list")
    selected = [
        record
        for record in records
        if isinstance(record, dict) and str(record.get("question_id")) == history_id
    ]
    if len(selected) != 1:
        raise ValueError("C0 calibration history is not unique")
    episodes = build_episodes(selected[0])
    if not isinstance(source_sequence, int) or not 0 <= source_sequence < len(episodes):
        raise ValueError("C0 source sequence is invalid")
    episode = episodes[source_sequence]
    if episode.source_hash != c0.get("episode_source_sha256"):
        raise ValueError("C0 episode source identity mismatch")
    namespace = str(c0.get("graph_namespace", ""))
    if re.fullmatch(r"nc-c0-[0-9a-f]{16}", namespace) is None:
        raise ValueError("C0 graph namespace is invalid")
    return C0Invocation(
        episode=episode,
        graph_namespace=namespace,
        freeze_payload_sha256=str(freeze["payload_sha256"]),
        freeze_file_sha256=_sha256_file(freeze_path),
    )


async def _ensure_driver_ready(graphiti: Any) -> None:
    driver = graphiti.driver
    init_task = getattr(driver, "_init_task", None)
    if init_task is not None:
        await init_task
        return
    readiness = getattr(driver, "build_indices_and_constraints", None)
    if not callable(readiness):
        raise RuntimeError("C0 driver has no readiness path")
    await readiness()


def _result_counts(value: Any) -> dict[str, int]:
    names = ("nodes", "edges", "episodic_edges", "communities", "community_edges")
    counts: dict[str, int] = {}
    for name in names:
        item = getattr(value, name, None)
        counts[name] = len(item) if isinstance(item, (list, tuple)) else 0
    return counts


def _build_result(
    invocation: C0Invocation,
    *,
    runtime_config: Mapping[str, Any],
    latency_ns: int,
    output: Any,
    error: BaseException | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "membind.native-characterization-c0-result.v1",
        "artifact_id": "native-characterization-c0",
        "run_id": invocation.run_id,
        "stage": "C0",
        "status": "pass" if error is None else "error",
        "interpretation": "engineering_viability_only_not_research_result",
        "history_id": invocation.episode.question_id,
        "source_sequence": invocation.episode.source_sequence,
        "episode_source_sha256": invocation.episode.source_hash,
        "graph_namespace": invocation.graph_namespace,
        "freeze_payload_sha256": invocation.freeze_payload_sha256,
        "freeze_file_sha256": invocation.freeze_file_sha256,
        "add_episode_latency_ns": int(latency_ns),
        "result_counts": _result_counts(output),
        "error_code": (
            None
            if error is None
            else f"{type(error).__module__}.{type(error).__qualname__}"
        ),
        "runtime_config": dict(runtime_config),
    }
    _assert_sanitized(result)
    result["payload_sha256"] = _sha256_bytes(_canonical_bytes(result))
    return result


def validate_c0_result(result: Mapping[str, Any]) -> None:
    if not isinstance(result, Mapping):
        raise ValueError("C0 result must be an object")
    candidate = deepcopy(dict(result))
    observed = candidate.pop("payload_sha256", None)
    _assert_sanitized(candidate)
    if observed != _sha256_bytes(_canonical_bytes(candidate)):
        raise ValueError("C0 payload_sha256 mismatch")
    if candidate.get("schema_version") != "membind.native-characterization-c0-result.v1":
        raise ValueError("C0 result schema mismatch")
    if _RUN_ID_RE.fullmatch(str(candidate.get("run_id", ""))) is None:
        raise ValueError("C0 run ID invalid")
    if candidate.get("status") not in {"pass", "error"}:
        raise ValueError("C0 status invalid")


async def execute_c0(
    *,
    authorization_checker: Callable[[LiveAction], Any] = require_live_action,
    invocation_loader: Callable[[], C0Invocation] | None = None,
    runtime_factory: Callable[..., Any] = build_u0_graphiti_from_env,
    result_sink: Callable[[Mapping[str, Any]], Any] | None = None,
    validation_root: str | Path | None = None,
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    """Execute one add_episode and persist a sanitized result before rethrow."""

    authorization_checker(LiveAction.NATIVE_CHARACTERIZATION_C0)
    validation = (
        Path(validation_root).resolve()
        if validation_root is not None
        else Path(__file__).resolve().parents[1]
    )
    source = (
        Path(source_path).resolve()
        if source_path is not None
        else Path(
            "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
            "longmemeval_s_cleaned.json"
        )
    )
    invocation = (
        invocation_loader()
        if invocation_loader is not None
        else prepare_c0_invocation(validation_root=validation, source_path=source)
    )
    runtime = runtime_factory(authorization_checker=authorization_checker)

    output: Any = None
    failure: BaseException | None = None
    latency_ns = 0
    start_ns: int | None = None
    try:
        try:
            await _ensure_driver_ready(runtime.graphiti)
            from graphiti_native import graphiti_episode_kwargs

            episode = replace(invocation.episode, group_id=invocation.graph_namespace)
            start_ns = time.monotonic_ns()
            output = await runtime.graphiti.add_episode(
                **graphiti_episode_kwargs(episode)
            )
        except BaseException as exc:
            failure = exc
        finally:
            if start_ns is not None:
                latency_ns = time.monotonic_ns() - start_ns
    finally:
        try:
            await runtime.graphiti.close()
        except BaseException as close_error:
            if failure is None:
                failure = close_error

    result = _build_result(
        invocation,
        runtime_config=runtime.config.to_artifact(),
        latency_ns=latency_ns,
        output=output,
        error=failure,
    )
    if result_sink is None:
        write_c0_result(
            result,
            validation / "artifacts/native_characterization/runs",
        )
    else:
        result_sink(result)
    if failure is not None:
        raise failure
    return result


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


def write_c0_result(
    result: Mapping[str, Any], output_root: str | Path
) -> dict[str, str]:
    """Write the manifest and resume checkpoint without overwriting an attempt."""

    validate_c0_result(result)
    run_id = str(result["run_id"])
    run_dir = Path(output_root) / run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError("C0 run namespace already contains evidence")
    manifest = _canonical_bytes(result) + b"\n"
    checkpoint_payload: dict[str, Any] = {
        "schema_version": "membind.native-characterization-checkpoint.v1",
        "run_id": run_id,
        "stage": "C0",
        "status": result["status"],
        "completed_source_sequences": (
            [result["source_sequence"]] if result["status"] == "pass" else []
        ),
        "manifest_payload_sha256": result["payload_sha256"],
        "error_code": result["error_code"],
    }
    checkpoint_payload["payload_sha256"] = _sha256_bytes(
        _canonical_bytes(checkpoint_payload)
    )
    checkpoint = _canonical_bytes(checkpoint_payload) + b"\n"
    written: dict[str, str] = {}
    for name, encoded in (("manifest.json", manifest), ("checkpoint.json", checkpoint)):
        _atomic_write(run_dir / name, encoded)
        written[name] = _sha256_bytes(encoded)
    return written


def _main() -> int:
    validation = Path(__file__).resolve().parents[1]
    source = Path(
        "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
        "longmemeval_s_cleaned.json"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        invocation = prepare_c0_invocation(
            validation_root=validation,
            source_path=source,
        )
        print(
            json.dumps(
                invocation.to_preview(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    try:
        result = asyncio.run(
            execute_c0(validation_root=validation, source_path=source)
        )
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_code": f"{type(exc).__module__}.{type(exc).__qualname__}",
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "run_id": result["run_id"],
                "payload_sha256": result["payload_sha256"],
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
