"""Load the common backend/client contracts frozen for SFWB v1.3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class BackendContractError(ValueError):
    """A machine-readable common contract is missing or malformed."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise BackendContractError(f"CONTRACT_UNREADABLE:{path.name}") from None
    if not isinstance(value, dict):
        raise BackendContractError(f"CONTRACT_OBJECT_REQUIRED:{path.name}")
    return value


def load_frozen_contracts(config_dir: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read both contracts and fail closed on method-specific overrides."""

    root = Path(config_dir)
    backend = _read_object(root / "frozen_backend_v1_3.json")
    client = _read_object(root / "frozen_client_v1_3.json")
    if backend.get("schema_version") != "sfwb.v1.3.frozen-backend.v1":
        raise BackendContractError("BACKEND_CONTRACT_SCHEMA_INVALID")
    if client.get("schema_version") != "sfwb.v1.3.frozen-client.v1":
        raise BackendContractError("CLIENT_CONTRACT_SCHEMA_INVALID")
    methods = client.get("methods")
    common = client.get("common")
    if not isinstance(methods, dict) or not isinstance(common, dict):
        raise BackendContractError("CLIENT_METHOD_CONTRACT_INVALID")
    expected_methods = {"B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC"}
    if set(methods) != expected_methods or any(value != common for value in methods.values()):
        raise BackendContractError("METHOD_SPECIFIC_CLIENT_OVERRIDE")
    if client.get("method_overrides") != {}:
        raise BackendContractError("METHOD_SPECIFIC_OVERRIDE_FORBIDDEN")
    if backend.get("method_overrides") != {}:
        raise BackendContractError("METHOD_SPECIFIC_BACKEND_OVERRIDE")
    for name in ("construction", "embedding"):
        if not isinstance(backend.get(name), dict):
            raise BackendContractError("BACKEND_ENDPOINT_CONTRACT_INVALID")
    return backend, client


__all__ = ["BackendContractError", "load_frozen_contracts"]
