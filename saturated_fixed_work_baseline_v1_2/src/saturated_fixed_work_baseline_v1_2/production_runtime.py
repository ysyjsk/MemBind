"""Thin composition of the pinned native Graphiti runtime for this protocol."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .reuse import import_validation_module
from .transport import install_runtime_cache_salt


PROTOCOL_VERSION = "SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_2"


class ProductionRuntimeError(ValueError):
    """The protocol authority or pinned runtime composition failed closed."""


def _read_authority(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProductionRuntimeError("LIVE_AUTHORITY_UNREADABLE") from None
    if not isinstance(value, dict):
        raise ProductionRuntimeError("LIVE_AUTHORITY_INVALID")
    if value.get("protocol_version") != PROTOCOL_VERSION:
        raise ProductionRuntimeError("LIVE_AUTHORITY_PROTOCOL_MISMATCH")
    for field in ("run_id", "block_id", "namespace"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ProductionRuntimeError("LIVE_AUTHORITY_IDENTITY_INVALID")
    return value


def build_protocol_runtime(
    *,
    repository_root: Path,
    cache_salt: str,
    authority_path: Path,
    builder: Callable[..., Any] | None = None,
    env_loader: Callable[[], Any] | None = None,
    live_action: Any | None = None,
    salt_installer: Callable[[Any, str], Any] = install_runtime_cache_salt,
) -> Any:
    """Build the native runtime after durable protocol authority, then add salt."""

    authority = _read_authority(authority_path)
    if builder is None or env_loader is None or live_action is None:
        runtime_module = import_validation_module(
            repository_root, "native_characterization_runtime"
        )
        gate_module = import_validation_module(repository_root, "current_state_gate")
        native_module = import_validation_module(repository_root, "graphiti_native")
        builder = builder or runtime_module.build_u0_graphiti_from_env
        live_action = live_action or gate_module.LiveAction.NATIVE_CHARACTERIZATION_C0
        env_path = repository_root / "membind-validation/.env"
        env_loader = env_loader or (lambda: native_module.load_env_file(env_path))

    def authorize(action: Any) -> dict[str, str]:
        observed = getattr(action, "value", action)
        expected = getattr(live_action, "value", live_action)
        if observed != expected:
            raise ProductionRuntimeError("LIVE_ACTION_MISMATCH")
        if not authority_path.is_file():
            raise ProductionRuntimeError("LIVE_AUTHORITY_LOST")
        current = _read_authority(authority_path)
        if current != authority:
            raise ProductionRuntimeError("LIVE_AUTHORITY_DRIFT")
        return {
            "status": "SATURATED_FIXED_WORK_BLOCK_AUTHORIZED",
            "block_id": str(authority["block_id"]),
        }

    runtime = builder(
        authorization_checker=authorize,
        live_action=live_action,
        env_loader=env_loader,
        structured_output_mode="json_schema",
    )
    salted = salt_installer(runtime, cache_salt)
    if salted is not runtime:
        raise ProductionRuntimeError("CACHE_SALT_INSTALLER_REPLACED_RUNTIME")
    if getattr(runtime, "graphiti", None) is None:
        raise ProductionRuntimeError("GRAPHITI_RUNTIME_MISSING")
    return runtime


__all__ = [
    "PROTOCOL_VERSION",
    "ProductionRuntimeError",
    "build_protocol_runtime",
]
