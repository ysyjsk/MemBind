"""Read-only vLLM metadata probing with strict credential redaction.

The fixed endpoint set contains no generation route. Server information is
reduced to an explicit allowlist before persistence; environment blocks and
unknown future fields are never copied into artifacts.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit


SCHEMA_VERSION = "membind.v3.vllm_metadata_probe.v1"
METADATA_PATHS = (
    "/version",
    "/v1/models",
    "/server_info?config_format=json",
    "/health",
)
_REQUIRED_PATHS = {"/version", "/v1/models", "/health"}
_STRUCTURED_CONFIG_FIELDS = (
    "backend",
    "disable_any_whitespace",
    "disable_additional_properties",
    "reasoning_parser",
    "reasoning_parser_plugin",
    "enable_in_reasoning",
)
_MODEL_CONFIG_FIELDS = (
    "model",
    "served_model_name",
    "dtype",
    "max_model_len",
    "revision",
    "tokenizer_mode",
    "trust_remote_code",
    "quantization",
)


def _server_root(base_url: str) -> str:
    parsed = urlsplit(str(base_url).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("vLLM base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("vLLM base URL must not contain credentials, query, or fragment")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))


def _content_type(headers: Any) -> str:
    value = headers.get("Content-Type", "") if headers is not None else ""
    return str(value).split(";", 1)[0].strip().lower()


def _decode_json(body: bytes, content_type: str) -> Any:
    if "json" not in content_type:
        return None
    return json.loads(body.decode("utf-8"))


def _allowlist(source: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {field: source[field] for field in fields if field in source}


def _safe_models(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    result = []
    for model in payload["data"]:
        if not isinstance(model, dict):
            continue
        result.append(
            {
                key: model[key]
                for key in ("id", "max_model_len", "root", "parent")
                if key in model
            }
        )
    return result


def _safe_server_config(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    config = payload.get("vllm_config")
    if not isinstance(config, dict):
        return None
    return {
        "structured_outputs_config": _allowlist(
            config.get("structured_outputs_config"),
            _STRUCTURED_CONFIG_FIELDS,
        ),
        "model_config": _allowlist(config.get("model_config"), _MODEL_CONFIG_FIELDS),
    }


def probe_vllm_metadata(
    base_url: str,
    api_key: str | None,
    *,
    timeout: float = 5.0,
    open_url: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Probe fixed metadata endpoints without issuing any generation request."""

    if timeout <= 0:
        raise ValueError("metadata probe timeout must be positive")
    root = _server_root(base_url)
    target_host = urlsplit(root).hostname or ""
    try:
        private_target = ipaddress.ip_address(target_host).is_private
    except ValueError:
        private_target = False
    proxy_bypass_for_target = bool(urllib.request.proxy_bypass(target_host))
    route_contract_ok = not private_target or proxy_bypass_for_target
    common = {
        "schema_version": SCHEMA_VERSION,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_metadata_no_generation",
        "base_url": root + "/v1",
        "requested_paths": list(METADATA_PATHS),
        "generation_endpoint_called": False,
        "secrets_persisted": False,
        "private_target": private_target,
        "proxy_bypass_for_target": proxy_bypass_for_target,
        "route_contract_ok": route_contract_ok,
    }
    if not route_contract_ok:
        return {
            **common,
            "ok": False,
            "blocker": "private_target_not_in_no_proxy",
            "version": None,
            "models": [],
            "server_config_available": False,
            "server_config": None,
            "endpoint_results": [],
        }
    endpoint_results: list[dict[str, Any]] = []
    version: str | None = None
    models: list[dict[str, Any]] = []
    server_config: dict[str, Any] | None = None

    for path in METADATA_PATHS:
        request = urllib.request.Request(root + path)
        if api_key:
            request.add_header("Authorization", "Bearer " + api_key)
        started = time.monotonic()
        try:
            with open_url(request, timeout=timeout) as response:
                body = response.read()
                content_type = _content_type(response.headers)
                status = int(response.status)
            result = {
                "path": path,
                "status": status,
                "ok": 200 <= status < 300,
                "content_type": content_type,
                "body_bytes": len(body),
                "body_sha256": hashlib.sha256(body).hexdigest(),
            }
            parsed = _decode_json(body, content_type)
            if path == "/version" and isinstance(parsed, dict):
                value = parsed.get("version")
                version = str(value) if value is not None else None
            elif path == "/v1/models":
                models = _safe_models(parsed)
            elif path.startswith("/server_info"):
                server_config = _safe_server_config(parsed)
        except urllib.error.HTTPError as exc:
            result = {
                "path": path,
                "status": int(exc.code),
                "ok": False,
                "error_type": "HTTPError",
            }
        except Exception as exc:
            result = {
                "path": path,
                "status": None,
                "ok": False,
                "error_type": type(exc).__name__,
            }
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000.0, 3)
        endpoint_results.append(result)

    status_by_path = {result["path"]: bool(result["ok"]) for result in endpoint_results}
    required_ok = all(status_by_path.get(path, False) for path in _REQUIRED_PATHS)
    return {
        **common,
        "ok": required_ok,
        "blocker": None if required_ok else "required_metadata_endpoint_failed",
        "version": version,
        "models": models,
        "server_config_available": server_config is not None,
        "server_config": server_config,
        "endpoint_results": endpoint_results,
    }


def write_vllm_metadata_probe(
    base_url: str,
    api_key: str | None,
    output: str | Path,
    *,
    timeout: float = 5.0,
    open_url: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Persist a sanitized metadata result using exclusive file creation."""

    payload = probe_vllm_metadata(
        base_url,
        api_key,
        timeout=timeout,
        open_url=open_url,
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    forbidden = ("Authorization", "api_key", "VLLM_API_KEY")
    if any(value.casefold() in encoded.casefold() for value in forbidden):
        raise ValueError("metadata artifact contains a forbidden credential field")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="ascii", newline="\n") as handle:
        handle.write(encoded)
    return payload
