"""Development-only compatibility probe for the SiliconFlow Qwen endpoint.

This module is intentionally separate from the frozen vLLM production runner.
It verifies that the provider can satisfy the *request contract* used by the
Graphiti NodeResolve path, but it does not claim scheduler, GPU, or performance
parity.  Credentials are accepted only as an in-process argument and are
never included in the returned or persisted projection.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.artifacts import atomic_write_json, payload_sha256


SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
SILICONFLOW_CHAT_MODEL = "Qwen/Qwen3-32B"
SILICONFLOW_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
SILICONFLOW_EMBEDDING_DIMENSION = 1024
SILICONFLOW_PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok"]},
        "count": {"type": "integer", "const": 1},
    },
    "required": ["status", "count"],
    "additionalProperties": False,
}


class SiliconFlowProbeError(ValueError):
    """A provider compatibility check failed closed."""


def _fail(code: str) -> SiliconFlowProbeError:
    return SiliconFlowProbeError(code)


def normalize_siliconflow_chat_request(
    request: Mapping[str, object],
) -> dict[str, object]:
    """Translate the frozen Qwen request shape to SiliconFlow's API shape.

    Graphiti/vLLM requests carry ``chat_template_kwargs`` and, for local
    vLLM, a ``cache_salt`` inside ``extra_body``.  SiliconFlow does not expose
    the latter and accepts the no-thinking switch as ``enable_thinking``.
    Other fields are preserved byte-for-byte at the Python value level.
    """

    if not isinstance(request, Mapping):
        raise _fail("request_invalid")
    normalized = deepcopy(dict(request))
    raw_extra = normalized.get("extra_body")
    if raw_extra is not None and not isinstance(raw_extra, Mapping):
        raise _fail("request_extra_body_invalid")
    extra = dict(raw_extra) if isinstance(raw_extra, Mapping) else {}
    template = extra.pop("chat_template_kwargs", None)
    if template is not None and not isinstance(template, Mapping):
        raise _fail("request_template_kwargs_invalid")
    enable_thinking: bool | None = None
    if isinstance(template, Mapping) and type(template.get("enable_thinking")) is bool:
        enable_thinking = bool(template["enable_thinking"])
    if type(extra.get("enable_thinking")) is bool:
        enable_thinking = bool(extra["enable_thinking"])
    # A top-level value is accepted for callers that already normalized the
    # request.  It is copied into extra_body so the OpenAI SDK merges it into
    # the actual JSON body at the provider boundary.
    if type(normalized.get("enable_thinking")) is bool:
        enable_thinking = bool(normalized.pop("enable_thinking"))
    extra.pop("cache_salt", None)
    extra["enable_thinking"] = False if enable_thinking is None else enable_thinking
    normalized["extra_body"] = extra
    return normalized


Transport = Callable[
    [str, str, dict[str, str], dict[str, object] | None, float], tuple[int, bytes]
]


def _http_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, object] | None,
    timeout: float,
) -> tuple[int, bytes]:
    data = None if body is None else json.dumps(
        body, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as error:
        # Do not retain or expose provider response bodies, which can contain
        # request-dependent text.  The status is enough for the public probe.
        raise _fail(f"http_status_{error.code}") from None
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        raise _fail(f"transport_{type(error).__name__}") from None


def _json_response(status: int, raw: bytes, code: str) -> Mapping[str, object]:
    if status != 200:
        raise _fail(f"{code}_http_{status}")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _fail(f"{code}_invalid_json") from None
    if not isinstance(value, Mapping):
        raise _fail(f"{code}_shape_invalid")
    return value


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _require_api_key(api_key: str) -> str:
    if not isinstance(api_key, str) or not api_key.strip():
        raise _fail("api_key_missing")
    return api_key


def _public_usage(usage: object) -> dict[str, int | None]:
    if not isinstance(usage, Mapping):
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    result: dict[str, int | None] = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(field)
        result[field] = value if isinstance(value, int) and not isinstance(value, bool) else None
    return result


def _wire_request(request: Mapping[str, object]) -> dict[str, object]:
    """Flatten SDK ``extra_body`` fields into the raw HTTP JSON body."""

    body = dict(request)
    extra = body.pop("extra_body", None)
    if isinstance(extra, Mapping):
        for key, value in extra.items():
            body[str(key)] = value
    return body


def run_siliconflow_probe(
    *,
    api_key: str,
    output_root: Path | None = None,
    transport: Transport | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, object]:
    """Run one bounded, content-safe provider compatibility probe.

    The probe performs exactly one model-list request, one embedding request,
    and one strict-schema chat request.  It does not contact Neo4j, Graphiti,
    or the v4 candidate runner.  ``output_root`` is optional so unit tests can
    exercise the same path without creating artifacts.
    """

    key = _require_api_key(api_key)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise _fail("timeout_invalid")
    if not math.isfinite(float(timeout_seconds)) or float(timeout_seconds) <= 0:
        raise _fail("timeout_invalid")
    selected = transport or _http_transport
    if not callable(selected):
        raise _fail("transport_invalid")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    base = SILICONFLOW_BASE_URL.rstrip("/")

    status, raw = selected("GET", f"{base}/models", headers, None, float(timeout_seconds))
    models_payload = _json_response(status, raw, "models")
    data = models_payload.get("data")
    if isinstance(data, (str, bytes)) or not isinstance(data, list):
        raise _fail("models_shape_invalid")
    model_ids = tuple(
        item.get("id")
        for item in data
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    )
    if SILICONFLOW_CHAT_MODEL not in model_ids or SILICONFLOW_EMBEDDING_MODEL not in model_ids:
        raise _fail("model_identity_mismatch")

    embedding_body = {
        "model": SILICONFLOW_EMBEDDING_MODEL,
        "input": "MemBind provider compatibility probe",
        "encoding_format": "float",
    }
    status, raw = selected(
        "POST", f"{base}/embeddings", headers, embedding_body, float(timeout_seconds)
    )
    embedding_payload = _json_response(status, raw, "embedding")
    embedding_data = embedding_payload.get("data")
    if (
        isinstance(embedding_data, (str, bytes))
        or not isinstance(embedding_data, list)
        or len(embedding_data) != 1
        or not isinstance(embedding_data[0], Mapping)
    ):
        raise _fail("embedding_shape_invalid")
    vector = embedding_data[0].get("embedding")
    if (
        isinstance(vector, (str, bytes))
        or not isinstance(vector, list)
        or len(vector) != SILICONFLOW_EMBEDDING_DIMENSION
        or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector)
    ):
        raise _fail("embedding_dimension_mismatch")
    if embedding_payload.get("model") not in {None, SILICONFLOW_EMBEDDING_MODEL}:
        raise _fail("embedding_model_mismatch")

    chat_body = normalize_siliconflow_chat_request(
        {
            "model": SILICONFLOW_CHAT_MODEL,
            "messages": [
                {"role": "system", "content": "Return JSON matching the schema."},
                {"role": "user", "content": "Set status to ok and count to 1."},
            ],
            "temperature": 0.0,
            "max_tokens": 128,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "membind_provider_probe",
                    "strict": True,
                    "schema": SILICONFLOW_PROBE_SCHEMA,
                },
            },
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
    )
    status, raw = selected(
        "POST",
        f"{base}/chat/completions",
        headers,
        _wire_request(chat_body),
        float(timeout_seconds),
    )
    chat_payload = _json_response(status, raw, "chat")
    if chat_payload.get("model") not in {None, SILICONFLOW_CHAT_MODEL}:
        raise _fail("chat_model_mismatch")
    choices = chat_payload.get("choices")
    if (
        isinstance(choices, (str, bytes))
        or not isinstance(choices, list)
        or not choices
        or not isinstance(choices[0], Mapping)
    ):
        raise _fail("chat_choices_invalid")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str):
        raise _fail("chat_content_invalid")
    try:
        parsed_content = json.loads(content)
    except json.JSONDecodeError:
        raise _fail("chat_schema_invalid") from None
    if parsed_content != {"status": "ok", "count": 1}:
        raise _fail("chat_schema_invalid")
    usage = _public_usage(chat_payload.get("usage"))
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.siliconflow-probe.v1",
        "status": "PASS",
        "provider": "SILICONFLOW_QWEN",
        "endpoint": SILICONFLOW_BASE_URL,
        "chat": {
            "model": SILICONFLOW_CHAT_MODEL,
            "strict_json_schema": True,
            "schema_sha256": _sha(SILICONFLOW_PROBE_SCHEMA),
            "schema_valid": True,
            "finish_reason": choices[0].get("finish_reason"),
            "response_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "usage": usage,
        },
        "embedding": {
            "model": SILICONFLOW_EMBEDDING_MODEL,
            "dimension": len(vector),
            "vector_sha256": hashlib.sha256(
                json.dumps(vector, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        },
        "formal_main_table_eligible": False,
        "development_only": True,
        "mutations_performed": False,
        "credentials_recorded": False,
        "api_key_source": "PROCESS_ARGUMENT_ONLY",
    }
    body["payload_sha256"] = payload_sha256(body)
    if output_root is not None:
        root = Path(output_root)
        root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(root / "SILICONFLOW_PROBE.json", body)
    return body


__all__ = [
    "SILICONFLOW_BASE_URL",
    "SILICONFLOW_CHAT_MODEL",
    "SILICONFLOW_EMBEDDING_MODEL",
    "SILICONFLOW_EMBEDDING_DIMENSION",
    "SILICONFLOW_PROBE_SCHEMA",
    "SiliconFlowProbeError",
    "normalize_siliconflow_chat_request",
    "run_siliconflow_probe",
]
