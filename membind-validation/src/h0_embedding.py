"""Strict, offline-constructible embedding adapter for H0-B and H0-C.

The adapter accepts only an already-resolved endpoint/model binding and
explicit credentials supplied by a gated caller.  It never reads ``.env`` and
persists only hashes, counts, dimensions, and L2 norms.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import hashlib
import json
import math
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

import httpx
from graphiti_core.embedder import EmbedderClient
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

from h0_runtime import H0InfrastructureError, H0ManifestError


EMBEDDING_DIMENSION = 1024
L2_NORM_ABS_TOLERANCE = 1e-5


class H0EmbeddingValidationError(H0ManifestError):
    """A sanitized endpoint, request, or embedding-output contract failure."""


def _fail(reason: str) -> H0EmbeddingValidationError:
    return H0EmbeddingValidationError(f"H0 embedding validation denied: {reason}")


def _normalized_v1_base_url(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(f"{label}_invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        raise _fail(f"{label}_not_bound_v1_url")
    return urlunsplit((parsed.scheme, parsed.netloc, "/v1/", "", ""))


def _require_count(value: Any, expected: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise _fail(f"{label}_mismatch")


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def build_graphiti_create_interface_evidence(
    *,
    input_data: object,
    http_attempt_count: int,
) -> dict[str, object]:
    """Project the historical pre-request Graphiti interface mismatch safely."""

    if (
        not isinstance(input_data, list)
        or len(input_data) != 1
        or not isinstance(input_data[0], str)
        or not input_data[0]
        or isinstance(http_attempt_count, bool)
        or http_attempt_count != 0
    ):
        raise _fail("graphiti_create_interface_event_mismatch")
    return {
        "schema_version": "membind.h0.harness-interface-evidence.v1",
        "failure_origin": "execution_harness_interface_contract",
        "failed_boundary": "graphiti_core.embedder.EmbedderClient.create",
        "observed_input_container": "list",
        "observed_input_count": 1,
        "http_attempt_count": 0,
        "candidate_model_failure_supported": False,
        "model_response_content_causally_relevant": False,
        "partial_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "secrets_persisted": False,
        "raw_inputs_persisted": False,
        "raw_responses_persisted": False,
    }


class H0EmbeddingAdapter(EmbedderClient):
    """One no-retry embedding client with strict vector and evidence checks."""

    def __init__(
        self,
        *,
        binding: Mapping[str, Any],
        credentials: Mapping[str, Any],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not isinstance(binding, Mapping) or not isinstance(credentials, Mapping):
            raise _fail("binding_or_credentials_not_mapping")
        bound_base_url = _normalized_v1_base_url(
            binding.get("base_url"), label="bound_base_url"
        )
        credential_base_url = _normalized_v1_base_url(
            credentials.get("base_url"), label="credential_base_url"
        )
        bound_model = binding.get("served_model_id")
        bound_vllm_version = binding.get("vllm_version")
        credential_model = credentials.get("model")
        api_key = credentials.get("api_key")
        if (
            bound_base_url != credential_base_url
            or not isinstance(bound_model, str)
            or not bound_model
            or credential_model != bound_model
            or bound_vllm_version != "0.26.0"
        ):
            raise _fail("endpoint_or_model_differs_from_binding")
        _require_count(binding.get("dimension"), EMBEDDING_DIMENSION, "dimension")
        if binding.get("normalization") != "l2":
            raise _fail("normalization_not_l2")
        if not isinstance(api_key, str) or not api_key:
            raise _fail("api_key_missing")

        timeout = httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=120.0)
        self._http_client = httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=bound_base_url,
            timeout=timeout,
            max_retries=0,
            http_client=self._http_client,
        )
        # The bound deployment is Linux. Avoid the SDK's unrelated blocking
        # platform probe before the first embedding HTTP request.
        self._client._platform = "Linux"  # type: ignore[attr-defined]
        self._model = bound_model
        self._vllm_version = bound_vllm_version
        self._origin = bound_base_url.removesuffix("v1/").rstrip("/")
        self._readiness_headers = {"Authorization": f"Bearer {api_key}"}
        self.config = SimpleNamespace(embedding_dim=EMBEDDING_DIMENSION)
        self._evidence: list[dict[str, Any]] = []
        self._readiness_performed = False
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise _fail("adapter_closed")

    @staticmethod
    def _texts(value: Any, *, batch: bool) -> list[str]:
        if batch:
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(item, str) or not item for item in value)
            ):
                raise _fail("batch_input_invalid")
            return list(value)
        if not isinstance(value, str) or not value:
            raise _fail("single_input_invalid")
        return [value]

    @staticmethod
    def _validated_vector(value: Any) -> tuple[list[float], float, str]:
        if not isinstance(value, (list, tuple)) or len(value) != EMBEDDING_DIMENSION:
            raise _fail("embedding_dimension_mismatch")
        vector: list[float] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise _fail("embedding_value_not_numeric")
            converted = float(item)
            if not math.isfinite(converted):
                raise _fail("embedding_value_not_finite")
            vector.append(converted)
        norm = math.sqrt(math.fsum(component * component for component in vector))
        if not math.isfinite(norm) or abs(norm - 1.0) > L2_NORM_ABS_TOLERANCE:
            raise _fail("embedding_l2_norm_mismatch")
        return vector, norm, _sha256_json(vector)

    async def _request(self, texts: list[str]) -> tuple[list[list[float]], dict[str, Any]]:
        self._ensure_open()
        request_input: str | list[str] = texts[0] if len(texts) == 1 else texts
        try:
            response = await self._client.embeddings.create(
                input=request_input,
                model=self._model,
            )
        except (APIConnectionError, APITimeoutError) as exc:
            raise H0InfrastructureError(
                "embedding_unreachable: stop_and_report"
            ) from exc
        except APIStatusError as exc:
            if exc.status_code == 429 or 500 <= exc.status_code <= 599:
                raise H0InfrastructureError(
                    "embedding_unreachable: stop_and_report"
                ) from exc
            raise _fail("embedding_http_contract_failure") from exc
        except Exception as exc:
            raise _fail("embedding_response_invalid") from exc

        data = getattr(response, "data", None)
        if not isinstance(data, list) or len(data) != len(texts):
            raise _fail("embedding_response_count_mismatch")
        vectors: list[list[float]] = []
        norms: list[float] = []
        vector_hashes: list[str] = []
        for expected_index, item in enumerate(data):
            if getattr(item, "index", None) != expected_index:
                raise _fail("embedding_response_order_mismatch")
            vector, norm, digest = self._validated_vector(
                getattr(item, "embedding", None)
            )
            vectors.append(vector)
            norms.append(norm)
            vector_hashes.append(digest)
        encoded_inputs = [text.encode("utf-8") for text in texts]
        evidence: dict[str, Any] = {
            "request_count": 1,
            "input_count": len(texts),
            "input_utf8_byte_count": sum(len(value) for value in encoded_inputs),
            "input_sha256": _sha256_json(
                [hashlib.sha256(value).hexdigest() for value in encoded_inputs]
            ),
            "embedding_count": len(vectors),
            "embedding_sha256": _sha256_json(vector_hashes),
            "http_attempt_count": 1,
            "llm_request_count": 0,
        }
        if len(vectors) == 1:
            evidence.update({"dimension": EMBEDDING_DIMENSION, "l2_norm": norms[0]})
        else:
            evidence.update(
                {
                    "dimensions": [EMBEDDING_DIMENSION] * len(vectors),
                    "l2_norms": norms,
                }
            )
        self._evidence.append(deepcopy(evidence))
        return vectors, evidence

    async def readiness(self) -> dict[str, Any]:
        """Verify endpoint metadata without issuing an embedding warm-up."""

        self._ensure_open()
        if self._readiness_performed:
            raise _fail("readiness_already_performed")
        self._readiness_performed = True
        responses: list[httpx.Response] = []
        for path in ("/version", "/v1/models", "/health"):
            try:
                response = await self._http_client.get(
                    f"{self._origin}{path}",
                    headers=self._readiness_headers,
                )
            except httpx.TransportError as exc:
                raise H0InfrastructureError(
                    "embedding_unreachable: stop_and_report"
                ) from exc
            if response.status_code == 429 or response.status_code >= 500:
                raise H0InfrastructureError(
                    "embedding_unreachable: stop_and_report"
                )
            if response.status_code != 200:
                raise _fail("embedding_readiness_http_contract_failure")
            responses.append(response)
        try:
            version_payload = responses[0].json()
            models_payload = responses[1].json()
        except ValueError:
            raise _fail("embedding_readiness_invalid_json") from None
        observed_version = (
            version_payload.get("version")
            if isinstance(version_payload, Mapping)
            else None
        )
        raw_models = (
            models_payload.get("data") if isinstance(models_payload, Mapping) else None
        )
        model_ids = {
            item.get("id")
            for item in raw_models
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        } if isinstance(raw_models, list) else set()
        if observed_version != self._vllm_version:
            raise _fail("embedding_readiness_vllm_version_mismatch")
        if self._model not in model_ids:
            raise _fail("embedding_readiness_served_model_mismatch")
        evidence = {
            "event": "embedding_metadata_readiness",
            "request_count": 3,
            "http_attempt_count": 3,
            "embedding_request_count": 0,
            "llm_request_count": 0,
            "served_model_id": self._model,
            "vllm_version": self._vllm_version,
            "warmup_performed": False,
        }
        self._evidence.append(deepcopy(evidence))
        return deepcopy(evidence)

    async def create(self, input_data: str | list[str]) -> list[float]:
        """Embed Graphiti's string or exact one-item list as one vector."""

        if isinstance(input_data, list):
            if len(input_data) != 1:
                raise _fail("single_input_invalid")
            texts = self._texts(input_data, batch=True)
        else:
            texts = self._texts(input_data, batch=False)
        vectors, _ = await self._request(texts)
        return vectors[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        """Embed a nonempty batch and validate every returned vector."""

        vectors, _ = await self._request(self._texts(input_data_list, batch=True))
        return vectors

    def safe_evidence(self) -> list[dict[str, Any]]:
        return deepcopy(self._evidence)

    async def close(self) -> None:
        """Best-effort, idempotent closure of both owned client layers."""

        if self._closed:
            return
        self._closed = True
        self._readiness_headers.clear()
        try:
            await self._client.close()
        except Exception:
            pass
        try:
            await self._http_client.aclose()
        except Exception:
            pass

    async def __aenter__(self) -> "H0EmbeddingAdapter":
        self._ensure_open()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        await self.close()
