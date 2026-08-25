"""SiliconFlow-ready V7 live-runner contract.

The runner is deliberately conservative: dry-run is the default, a sealed
method selection is mandatory for any live call, and the API key is read only
from an environment variable.  It is a transport/orchestration shell for the
future GPU campaign, not an unapproved V7 treatment implementation.
"""

from __future__ import annotations

import inspect
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping


class V7LiveRunnerError(RuntimeError):
    pass


SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
V7_METHODS = frozenset({"OBSERVER_ONLY", "M0", "M1", "M2", "NULL"})


@dataclass(frozen=True, slots=True)
class V7LiveConfig:
    output_root: Path
    run_id: str
    method: str = "OBSERVER_ONLY"
    dry_run: bool = True
    gate_path: Path | None = None
    api_key_env: str = "SILICONFLOW_API_KEY"
    construction_base_url: str = SILICONFLOW_BASE_URL
    embedding_base_url: str = SILICONFLOW_BASE_URL
    construction_model: str = ""
    embedding_model: str = ""
    source_count: int = 2

    def __post_init__(self) -> None:
        if not re.fullmatch(r"v7-[a-z0-9][a-z0-9-]{2,79}", self.run_id):
            raise V7LiveRunnerError("V7 run_id is invalid")
        if self.method not in V7_METHODS:
            raise V7LiveRunnerError("V7 method is not recognized")
        if not isinstance(self.source_count, int) or self.source_count <= 0:
            raise V7LiveRunnerError("source_count must be positive")
        if not self.dry_run and self.method in {"OBSERVER_ONLY", "NULL"}:
            raise V7LiveRunnerError("live run requires a selected M0, M1 or M2 method")
        if not self.api_key_env or "=" in self.api_key_env:
            raise V7LiveRunnerError("api_key_env must be an environment variable name")


def _api_key(config: V7LiveConfig) -> str | None:
    value = os.environ.get(config.api_key_env)
    return value if value else None


def redact_config(config: V7LiveConfig) -> dict[str, Any]:
    """Return a manifest-safe config projection; never include secret bytes."""

    return {
        "run_id": config.run_id,
        "method": config.method,
        "dry_run": config.dry_run,
        "api_key_env": config.api_key_env,
        "api_key_present": _api_key(config) is not None,
        "construction_base_url": config.construction_base_url,
        "embedding_base_url": config.embedding_base_url,
        "construction_model": config.construction_model,
        "embedding_model": config.embedding_model,
        "source_count": config.source_count,
    }


def _read_gate(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise V7LiveRunnerError("method selection seal is missing") from exc
    except json.JSONDecodeError as exc:
        raise V7LiveRunnerError("method selection seal is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise V7LiveRunnerError("method selection seal must be an object")
    return value


def validate_live_gate(config: V7LiveConfig) -> Mapping[str, Any] | None:
    if config.dry_run:
        return None
    if config.gate_path is None:
        raise V7LiveRunnerError("method selection seal is required before live run")
    gate = _read_gate(Path(config.gate_path))
    authorized = gate.get("authorized") is True or gate.get("status") in {"PASS", "AUTHORIZED", "V7_POSITIVE_M1_SEMANTIC_MAINTENANCE", "V7_POSITIVE_M2_PERSISTENT_TRANSITION", "V7_EXACT_REPLAY_ONLY"}
    selected = gate.get("selected_method") or gate.get("method") or gate.get("winner")
    if not authorized or selected != config.method:
        raise V7LiveRunnerError("method selection seal does not authorize the requested method")
    if gate.get("treatment_authorized") is False:
        raise V7LiveRunnerError("method selection seal keeps treatment disabled")
    return gate


def build_siliconflow_client(config: V7LiveConfig) -> Any:
    """Build an OpenAI-compatible async client without logging the API key."""

    key = _api_key(config)
    if key is None:
        raise V7LiveRunnerError(f"{config.api_key_env} is required for a live provider call")
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise V7LiveRunnerError("openai package is required for SiliconFlow live run") from exc
    return AsyncOpenAI(api_key=key, base_url=config.construction_base_url)


def build_siliconflow_embedding_client(config: V7LiveConfig) -> Any:
    """Build the same OpenAI-compatible client against the embedding endpoint."""

    key = _api_key(config)
    if key is None:
        raise V7LiveRunnerError(f"{config.api_key_env} is required for a live provider call")
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise V7LiveRunnerError("openai package is required for SiliconFlow live run") from exc
    return AsyncOpenAI(api_key=key, base_url=config.embedding_base_url)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise V7LiveRunnerError(f"artifact already exists: {path}")
    path.write_text(json.dumps(dict(value), ensure_ascii=True, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8")


async def run_v7_live_async(
    config: V7LiveConfig,
    *,
    provider_call: Callable[[], Awaitable[Any] | Any] | None = None,
) -> dict[str, Any]:
    """Run a dry-run or one explicitly authorized provider callback.

    The callback is dependency-injected so GPU integration can bind the
    existing Graphiti native runner after the method-selection gate. This
    function itself never skips native demand construction or publishes state.
    """

    gate = validate_live_gate(config)
    root = Path(config.output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise V7LiveRunnerError("V7 output root must be fresh")
    root.mkdir(parents=True, exist_ok=True)
    if not config.dry_run:
        if _api_key(config) is None:
            raise V7LiveRunnerError(f"{config.api_key_env} is required for live provider call")
        if provider_call is None:
            raise V7LiveRunnerError("live runner requires an injected provider/native callback")
    status = "DRY_RUN"
    provider_calls = 0
    result: Any = None
    if not config.dry_run:
        status = "LIVE_AUTHORIZED"
        try:
            result = await _maybe_await(provider_call())
            provider_calls = 1
        except BaseException as exc:
            _write_new(
                root / "RUN_FAILURE.json",
                {
                    "schema_version": "membind.v7.live-failure.v1",
                    "status": "FAILED",
                    "provider_calls": provider_calls,
                    "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "error_message_digest": hashlib.sha256(str(exc).encode("utf-8", errors="backslashreplace")).hexdigest(),
                },
            )
            raise
    manifest = {
        "schema_version": "membind.v7.live-runner-manifest.v1",
        "status": status,
        "provider": "siliconflow",
        "method": config.method,
        "provider_calls": provider_calls,
        "treatment_authorized": not config.dry_run,
        "gate_present": gate is not None,
        "config": redact_config(config),
        "result_type": None if result is None else f"{type(result).__module__}.{type(result).__qualname__}",
    }
    _write_new(root / "RUN_MANIFEST.json", manifest)
    _write_new(root / "RUN_STATE.json", {"schema_version": "membind.v7.live-run-state.v1", "status": status, "provider_calls": provider_calls, "next_action": "GPU live integration" if config.dry_run else "seal and inspect"})
    return manifest


def run_v7_live(config: V7LiveConfig, *, provider_call: Callable[[], Awaitable[Any] | Any] | None = None) -> dict[str, Any]:
    import asyncio

    return asyncio.run(run_v7_live_async(config, provider_call=provider_call))


__all__ = [
    "SILICONFLOW_BASE_URL",
    "V7LiveConfig",
    "V7LiveRunnerError",
    "build_siliconflow_client",
    "build_siliconflow_embedding_client",
    "redact_config",
    "run_v7_live",
    "run_v7_live_async",
    "validate_live_gate",
]
