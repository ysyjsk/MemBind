"""Direct service probes and strict response identity validation."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any


class ServiceEvidenceError(ValueError):
    """A service response was unavailable, malformed, or identity-drifted."""


def direct_get_text(url: str, *, timeout_s: float = 10.0) -> dict[str, Any]:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ServiceEvidenceError("SERVICE_URL_INVALID")
    if isinstance(timeout_s, bool) or timeout_s <= 0:
        raise ServiceEvidenceError("SERVICE_TIMEOUT_INVALID")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json,text/plain;q=0.9,*/*;q=0.1"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=float(timeout_s)) as response:
            body = response.read(20 * 1024 * 1024 + 1)
            status = int(response.status)
            content_type = str(response.headers.get("Content-Type") or "")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        raise ServiceEvidenceError("SERVICE_DIRECT_GET_FAILED") from None
    if status != 200 or len(body) > 20 * 1024 * 1024:
        raise ServiceEvidenceError("SERVICE_RESPONSE_INVALID")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ServiceEvidenceError("SERVICE_RESPONSE_NOT_UTF8") from None
    return {
        "schema_version": "membind.saturated-fixed-work.direct-http-evidence.v1",
        "url": url,
        "status_code": status,
        "content_type": content_type,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_bytes": len(body),
        "text": text,
        "proxy_policy": "DISABLED",
    }


def validate_model_catalog(
    payload: str | bytes | Mapping[str, Any],
    *,
    expected_model: str,
    expected_max_model_len: int,
    endpoint: str,
) -> dict[str, Any]:
    try:
        if isinstance(payload, bytes):
            value = json.loads(payload.decode("utf-8"))
        elif isinstance(payload, str):
            value = json.loads(payload)
        else:
            value = dict(payload)
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise ServiceEvidenceError("MODEL_CATALOG_INVALID") from None
    rows = value.get("data") if isinstance(value, Mapping) else None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise ServiceEvidenceError("MODEL_CATALOG_INVALID")
    row = rows[0]
    if row.get("id") != expected_model:
        raise ServiceEvidenceError("MODEL_ID_MISMATCH")
    if row.get("max_model_len") != expected_max_model_len:
        raise ServiceEvidenceError("MODEL_CONTEXT_MISMATCH")
    root = row.get("root")
    if not isinstance(root, str) or not root:
        raise ServiceEvidenceError("MODEL_ROOT_MISSING")
    return {
        "schema_version": "membind.saturated-fixed-work.model-catalog-evidence.v1",
        "status": "PASS",
        "endpoint": endpoint,
        "model": expected_model,
        "max_model_len": expected_max_model_len,
        "model_root": root,
        "response_sha256": hashlib.sha256(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
    }


def probe_model_catalog(
    endpoint: str,
    *,
    expected_model: str,
    expected_max_model_len: int,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    response = direct_get_text(endpoint, timeout_s=timeout_s)
    validated = validate_model_catalog(
        response["text"],
        expected_model=expected_model,
        expected_max_model_len=expected_max_model_len,
        endpoint=endpoint,
    )
    return {**validated, "http_evidence": {key: value for key, value in response.items() if key != "text"}}


__all__ = [
    "ServiceEvidenceError",
    "direct_get_text",
    "probe_model_catalog",
    "validate_model_catalog",
]
