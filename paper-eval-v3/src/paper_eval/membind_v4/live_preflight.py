"""Read-only v4 service preflight with explicit sandbox failure taxonomy.

The preflight never starts a process, reads credentials into a public artifact,
creates a namespace, or invokes a model.  It is deliberately usable before a
live candidate is admitted so a restricted execution sandbox cannot be
mistaken for a remote vLLM outage.
"""

from __future__ import annotations

import errno
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Mapping

from paper_eval.artifacts import payload_sha256


PREFLIGHT_SANDBOX_NETWORK_ISOLATION = "EXECUTION_SANDBOX_NETWORK_ISOLATION"
PREFLIGHT_REMOTE_UNAVAILABLE = "REMOTE_SERVICE_UNAVAILABLE"
PREFLIGHT_READY = "READY"

# Only non-sensitive deployment identity is allowed into a preflight result.
_PUBLIC_ENV_KEYS = {
    "CONSTRUCTION_LLM_BASE_URL",
    "CONSTRUCTION_LLM_MODEL",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "NEO4J_URI",
}


def read_env_file(path: Path) -> dict[str, str]:
    """Read only public endpoint/model keys from a dotenv file."""

    result: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in _PUBLIC_ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        result[key] = value
    return result


def classify_socket_error(error: BaseException) -> str:
    """Classify local socket denial separately from a reached remote outage."""

    if isinstance(error, PermissionError):
        return PREFLIGHT_SANDBOX_NETWORK_ISOLATION
    if isinstance(error, OSError) and getattr(error, "errno", None) in {
        errno.EPERM,
        errno.EACCES,
        errno.ENETUNREACH,
    }:
        return PREFLIGHT_SANDBOX_NETWORK_ISOLATION
    return PREFLIGHT_REMOTE_UNAVAILABLE


def _route_available() -> bool:
    """Return whether a usable non-loopback route is visible to this process.

    Linux represents the default route with destination ``00000000``.  The
    previous implementation excluded that row, which made a healthy host
    look network-isolated whenever it had only a default route.  Treat any
    non-loopback route marked ``RTF_UP`` as usable; an empty table still
    remains the explicit sandbox-isolation signal used by the read-only gate.
    """

    try:
        rows = Path("/proc/net/route").read_text(encoding="ascii").splitlines()[1:]
    except (OSError, UnicodeError):
        # Lack of proc visibility is not proof of isolation; let the actual
        # socket probe classify the environment.
        return True
    for row in rows:
        fields = row.split()
        if len(fields) < 4 or fields[0] in {"", "lo"}:
            continue
        try:
            flags = int(fields[3], 16)
        except ValueError:
            continue
        if flags & 0x1:  # RTF_UP
            return True
    return False


def _endpoint_status(url: str, *, timeout: float) -> dict[str, Any]:
    parsed = urlparse(url)
    public = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
    if not parsed.scheme or not parsed.hostname or parsed.port is None:
        return {"status": "INVALID_ENDPOINT", "endpoint": public}
    if not _route_available() and parsed.hostname not in {"127.0.0.1", "localhost"}:
        return {
            "status": "BLOCKED",
            "endpoint": public,
            "classification": PREFLIGHT_SANDBOX_NETWORK_ISOLATION,
        }
    try:
        request = urllib.request.Request(public, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            # Do not retain response bodies or headers; only the readiness code.
            return {"status": "READY" if 200 <= status < 300 else "HTTP_ERROR", "http_status": status, "endpoint": public}
    except (PermissionError, OSError) as error:
        return {"status": "BLOCKED", "endpoint": public, "classification": classify_socket_error(error)}
    except urllib.error.URLError as error:
        reason = error.reason if isinstance(error.reason, BaseException) else error
        return {"status": "BLOCKED", "endpoint": public, "classification": classify_socket_error(reason)}


def _neo4j_status(uri: str, *, timeout: float) -> dict[str, Any]:
    parsed = urlparse(uri)
    host = parsed.hostname or ""
    port = parsed.port or 7687
    public = f"{parsed.scheme}://{host}:{port}"
    if not host:
        return {"status": "INVALID_ENDPOINT", "endpoint": public}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"status": "READY", "endpoint": public}
    except (PermissionError, OSError) as error:
        return {"status": "BLOCKED", "endpoint": public, "classification": classify_socket_error(error)}


def probe_services(
    *,
    env: Mapping[str, str],
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Perform bounded, read-only readiness probes for both model services and Neo4j."""

    construction_base = str(env.get("CONSTRUCTION_LLM_BASE_URL", "")).rstrip("/") + "/models"
    embedding_base = str(env.get("EMBEDDING_BASE_URL", "")).rstrip("/") + "/models"
    construction = _endpoint_status(construction_base, timeout=timeout)
    embedding = _endpoint_status(embedding_base, timeout=timeout)
    neo4j = _neo4j_status(str(env.get("NEO4J_URI", "bolt://127.0.0.1:7687")), timeout=timeout)
    classifications = [
        item.get("classification")
        for item in (construction, embedding, neo4j)
        if item.get("classification")
    ]
    if PREFLIGHT_SANDBOX_NETWORK_ISOLATION in classifications:
        classification = PREFLIGHT_SANDBOX_NETWORK_ISOLATION
    elif all(item.get("status") == "READY" for item in (construction, embedding, neo4j)):
        classification = PREFLIGHT_READY
    else:
        classification = PREFLIGHT_REMOTE_UNAVAILABLE
    return build_preflight_artifact(
        construction=construction,
        embedding=embedding,
        neo4j=neo4j,
        classification=classification,
    )


def build_preflight_artifact(
    *,
    construction: Mapping[str, Any],
    embedding: Mapping[str, Any],
    neo4j: Mapping[str, Any],
    classification: str,
) -> dict[str, Any]:
    """Build a sealed public preflight artifact without secrets or response bodies."""

    if classification == PREFLIGHT_READY:
        status = "READY"
    else:
        status = "BLOCKED_SERVICE_PREFLIGHT"
    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v4.service-preflight.v1",
        "status": status,
        "classification": classification,
        "construction": dict(construction),
        "embedding": dict(embedding),
        "neo4j": dict(neo4j),
        "mutations_performed": False,
        "credentials_recorded": False,
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


__all__ = [
    "PREFLIGHT_READY",
    "PREFLIGHT_REMOTE_UNAVAILABLE",
    "PREFLIGHT_SANDBOX_NETWORK_ISOLATION",
    "build_preflight_artifact",
    "classify_socket_error",
    "probe_services",
    "read_env_file",
]
