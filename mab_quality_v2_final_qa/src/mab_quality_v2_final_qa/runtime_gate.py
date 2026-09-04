"""Explicit model-service gate; no inference is performed here."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from collections.abc import Mapping
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen


SILICONFLOW_PROVIDER = "SILICONFLOW_QWEN"
SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
SILICONFLOW_CHAT_MODEL = "Qwen/Qwen3-32B"
SILICONFLOW_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
SILICONFLOW_EMBEDDING_DIMENSION = 1024


@dataclass(frozen=True)
class ModelPortStatus:
    port: int
    available: bool
    http_status: int | None
    models: tuple[str, ...]
    error: str | None


@dataclass(frozen=True)
class EmbeddingEndpointStatus:
    available: bool
    http_status: int | None
    model: str
    dimension: int | None
    response_sha256: str | None
    error: str | None


@dataclass(frozen=True)
class RuntimeEndpoint:
    role: str
    base_url: str
    model: str

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"


@dataclass(frozen=True)
class RuntimeTopology:
    provider: str
    construction: RuntimeEndpoint
    quality: RuntimeEndpoint
    embedding: RuntimeEndpoint
    embedding_dimension: int
    neo4j_uri: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "RuntimeTopology":
        required = {
            "CONSTRUCTION_LLM_BASE_URL",
            "CONSTRUCTION_LLM_MODEL",
            "EMBEDDING_BASE_URL",
            "EMBEDDING_MODEL",
            "NEO4J_URI",
        }
        if not isinstance(env, Mapping) or any(not env.get(key) for key in required):
            raise ValueError("RUNTIME_TOPOLOGY_INCOMPLETE")
        construction_url = str(env["CONSTRUCTION_LLM_BASE_URL"]).rstrip("/")
        embedding_url = str(env["EMBEDDING_BASE_URL"]).rstrip("/")
        construction_model = str(env["CONSTRUCTION_LLM_MODEL"])
        embedding_model = str(env["EMBEDDING_MODEL"])
        try:
            embedding_dimension = int(env.get("EMBEDDING_DIM", "1024"))
        except (TypeError, ValueError):
            raise ValueError("EMBEDDING_DIMENSION_INVALID") from None
        provider = str(env.get("MAB_RUNTIME_PROVIDER", "FROZEN_V31"))
        if provider == SILICONFLOW_PROVIDER:
            quality_url = str(env.get("QUALITY_LLM_BASE_URL", "")).rstrip("/")
            quality_model = str(env.get("QUALITY_LLM_MODEL", ""))
            if construction_url != SILICONFLOW_BASE_URL:
                raise ValueError("SILICONFLOW_CONSTRUCTION_ENDPOINT_DRIFT")
            if construction_model != SILICONFLOW_CHAT_MODEL:
                raise ValueError("SILICONFLOW_CONSTRUCTION_MODEL_DRIFT")
            if quality_url != SILICONFLOW_BASE_URL:
                raise ValueError("SILICONFLOW_QUALITY_ENDPOINT_DRIFT")
            if quality_model != SILICONFLOW_CHAT_MODEL:
                raise ValueError("SILICONFLOW_QUALITY_MODEL_DRIFT")
            if embedding_url != SILICONFLOW_BASE_URL:
                raise ValueError("SILICONFLOW_EMBEDDING_ENDPOINT_DRIFT")
            if embedding_model != SILICONFLOW_EMBEDDING_MODEL:
                raise ValueError("SILICONFLOW_EMBEDDING_MODEL_DRIFT")
            if embedding_dimension != SILICONFLOW_EMBEDDING_DIMENSION:
                raise ValueError("SILICONFLOW_EMBEDDING_DIMENSION_DRIFT")
        elif provider == "LOCAL_DUAL_REPLICA":
            # The platform manifest authenticates the concrete deployment model.
            # This gate independently fixes the shared local service topology.
            quality_url = str(
                env.get("QUALITY_LLM_BASE_URL", construction_url)
            ).rstrip("/")
            quality_model = str(env.get("QUALITY_LLM_MODEL", construction_model))
            if construction_url != "http://127.0.0.1:18200/v1":
                raise ValueError(
                    "LOCAL_DUAL_REPLICA_CONSTRUCTION_ENDPOINT_DRIFT"
                )
            if quality_url != construction_url or quality_model != construction_model:
                raise ValueError("LOCAL_DUAL_REPLICA_QUALITY_ENDPOINT_DRIFT")
            if embedding_url != "http://127.0.0.1:18202/v1":
                raise ValueError("LOCAL_DUAL_REPLICA_EMBEDDING_ENDPOINT_DRIFT")
            if embedding_model != "qwen3-embedding-0.6b":
                raise ValueError("LOCAL_DUAL_REPLICA_EMBEDDING_MODEL_DRIFT")
            if embedding_dimension != 1024:
                raise ValueError("LOCAL_DUAL_REPLICA_EMBEDDING_DIMENSION_DRIFT")
        elif provider == "LOCAL_8B":
            # The formal three-arm 8B campaign uses the same local model for
            # construction, read-only QA and embeddings.  Keep this explicit
            # rather than allowing arbitrary endpoint drift through the QA
            # gate.
            quality_url = str(env.get("QUALITY_LLM_BASE_URL", construction_url)).rstrip("/")
            quality_model = str(env.get("QUALITY_LLM_MODEL", construction_model))
            if construction_url != "http://127.0.0.1:18200/v1":
                raise ValueError("LOCAL_8B_CONSTRUCTION_ENDPOINT_DRIFT")
            if construction_model != "qwen3-8b-awq":
                raise ValueError("LOCAL_8B_CONSTRUCTION_MODEL_DRIFT")
            if quality_url != construction_url or quality_model != construction_model:
                raise ValueError("LOCAL_8B_QUALITY_ENDPOINT_DRIFT")
            if embedding_url != "http://127.0.0.1:18202/v1":
                raise ValueError("LOCAL_8B_EMBEDDING_ENDPOINT_DRIFT")
            if embedding_model != "qwen3-embedding-0.6b":
                raise ValueError("LOCAL_8B_EMBEDDING_MODEL_DRIFT")
            if embedding_dimension != 1024:
                raise ValueError("LOCAL_8B_EMBEDDING_DIMENSION_DRIFT")
        elif provider == "FROZEN_V31":
            quality_url = str(
                env.get("QUALITY_LLM_BASE_URL", construction_url)
            ).rstrip("/")
            quality_model = str(env.get("QUALITY_LLM_MODEL", construction_model))
            if construction_url != "http://10.87.5.247:8000/v1":
                raise ValueError("CONSTRUCTION_ENDPOINT_DRIFT")
            if construction_model != "qwen3-32b-fp8":
                raise ValueError("CONSTRUCTION_MODEL_DRIFT")
            if quality_url != construction_url or quality_model != construction_model:
                raise ValueError("QUALITY_ENDPOINT_DRIFT")
            if embedding_url != "http://10.87.5.247:8001/v1":
                raise ValueError("EMBEDDING_ENDPOINT_DRIFT")
            if embedding_model != "qwen3-embedding-0.6b":
                raise ValueError("EMBEDDING_MODEL_DRIFT")
            if embedding_dimension != 1024:
                raise ValueError("EMBEDDING_DIMENSION_DRIFT")
        else:
            raise ValueError("RUNTIME_PROVIDER_INVALID")
        return cls(
            provider=provider,
            construction=RuntimeEndpoint(
                "construction", construction_url, construction_model
            ),
            quality=RuntimeEndpoint(
                "quality", quality_url, quality_model
            ),
            embedding=RuntimeEndpoint(
                "embedding", embedding_url, embedding_model
            ),
            embedding_dimension=embedding_dimension,
            neo4j_uri=str(env["NEO4J_URI"]),
        )

    def public_identity(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "construction": {
                "base_url": self.construction.base_url,
                "model": self.construction.model,
            },
            "quality": {
                "base_url": self.quality.base_url,
                "model": self.quality.model,
            },
            "embedding": {
                "base_url": self.embedding.base_url,
                "model": self.embedding.model,
                "dimension": self.embedding_dimension,
            },
            "neo4j": {"uri": self.neo4j_uri},
        }


def classify_probe_error(error: BaseException) -> str:
    message = str(error).casefold()
    if isinstance(error, PermissionError) or "operation not permitted" in message:
        return "EXECUTION_SANDBOX_NETWORK_ISOLATION"
    if isinstance(error, ConnectionRefusedError) or "connection refused" in message:
        return "ENDPOINT_CONNECTION_REFUSED"
    if isinstance(error, TimeoutError) or "timed out" in message:
        return "ENDPOINT_TIMEOUT"
    return "ENDPOINT_PROBE_FAILED"


def check_model_port(port: int, *, timeout: float = 2.0) -> ModelPortStatus:
    url = f"http://127.0.0.1:{port}/v1/models"
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            body = json.loads(response.read().decode("utf-8"))
        values = body.get("data", []) if isinstance(body, dict) else []
        models = tuple(
            str(item.get("id"))
            for item in values
            if isinstance(item, dict) and item.get("id")
        )
        return ModelPortStatus(port, status == 200, status, models, None)
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        return ModelPortStatus(
            port, False, None, (), f"{type(error).__name__}: {error}"
        )


def check_model_endpoint(
    base_url: str,
    *,
    expected_model: str | None = None,
    expected_models: tuple[str, ...] | None = None,
    api_key: str | None = None,
    timeout: float = 5.0,
    opener: object | None = None,
) -> ModelPortStatus:
    """Probe an explicit OpenAI-compatible endpoint with no proxy inheritance."""

    normalized = str(base_url).rstrip("/")
    if not normalized.endswith("/v1"):
        raise ValueError("MODEL_ENDPOINT_URL_INVALID")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MODEL_ENDPOINT_URL_INVALID")
    url = f"{normalized}/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(url, headers=headers, method="GET")
    selected_opener = opener or build_opener(ProxyHandler({}))
    try:
        with selected_opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            body = json.loads(response.read().decode("utf-8"))
        values = body.get("data", []) if isinstance(body, dict) else []
        models = tuple(
            str(item.get("id"))
            for item in values
            if isinstance(item, dict) and item.get("id")
        )
        required_models = tuple(expected_models or ())
        if expected_model is not None:
            required_models = (*required_models, expected_model)
        available = status == 200 and all(model in models for model in required_models)
        error = None if available else "MODEL_IDENTITY_MISMATCH"
        return ModelPortStatus(
            parsed.port or (443 if parsed.scheme == "https" else 80),
            available,
            status,
            models,
            error,
        )
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        return ModelPortStatus(
            parsed.port or (443 if parsed.scheme == "https" else 80),
            False,
            None,
            (),
            f"{classify_probe_error(error)}:{error}",
        )


def check_embedding_endpoint(
    base_url: str,
    *,
    model: str,
    expected_dimension: int,
    api_key: str,
    timeout: float = 30.0,
    opener: object | None = None,
) -> EmbeddingEndpointStatus:
    """Make one content-neutral embedding call and retain only public shape evidence."""

    normalized = str(base_url).rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or not normalized.endswith("/v1")
        or not model
        or expected_dimension <= 0
        or not api_key
    ):
        raise ValueError("EMBEDDING_ENDPOINT_CONFIG_INVALID")
    body = json.dumps(
        {"model": model, "input": "MemBind embedding dimension probe"},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    request = Request(
        f"{normalized}/embeddings",
        data=body,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    selected_opener = opener or build_opener(ProxyHandler({}))
    try:
        with selected_opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read()
        payload = json.loads(raw.decode("utf-8"))
        returned_model = str(payload.get("model") or model)
        data = payload.get("data")
        vector = data[0].get("embedding") if isinstance(data, list) and data else None
        dimension = len(vector) if isinstance(vector, list) else None
        available = (
            status == 200
            and returned_model == model
            and dimension == expected_dimension
            and all(isinstance(value, (int, float)) for value in vector or ())
        )
        return EmbeddingEndpointStatus(
            available=available,
            http_status=status,
            model=returned_model,
            dimension=dimension,
            response_sha256=hashlib.sha256(raw).hexdigest(),
            error=None if available else "EMBEDDING_IDENTITY_MISMATCH",
        )
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        return EmbeddingEndpointStatus(
            available=False,
            http_status=None,
            model=model,
            dimension=None,
            response_sha256=None,
            error=f"{classify_probe_error(error)}:{error}",
        )
def require_live_model_ports(
    *, ports: tuple[int, int] = (8002, 8003), timeout: float = 2.0
) -> tuple[ModelPortStatus, ...]:
    statuses = tuple(check_model_port(port, timeout=timeout) for port in ports)
    if not all(status.available for status in statuses):
        detail = "; ".join(
            f"{status.port}: {status.error or status.http_status}"
            for status in statuses
        )
        raise RuntimeError(f"LIVE_MODEL_GATE_FAILED:{detail}")
    return statuses


__all__ = [
    "ModelPortStatus",
    "EmbeddingEndpointStatus",
    "RuntimeEndpoint",
    "RuntimeTopology",
    "check_model_port",
    "check_model_endpoint",
    "check_embedding_endpoint",
    "classify_probe_error",
    "require_live_model_ports",
    "SILICONFLOW_PROVIDER",
    "SILICONFLOW_BASE_URL",
    "SILICONFLOW_CHAT_MODEL",
    "SILICONFLOW_EMBEDDING_MODEL",
    "SILICONFLOW_EMBEDDING_DIMENSION",
]
