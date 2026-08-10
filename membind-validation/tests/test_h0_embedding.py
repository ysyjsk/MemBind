"""Offline transport contracts for the H0-B/C embedding adapter.

All requests terminate in ``httpx.MockTransport``.  The tests never load the
project environment or contact an embedding, LLM, database, or SSH service.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_embedding import (  # noqa: E402
    EMBEDDING_DIMENSION,
    H0EmbeddingAdapter,
    H0EmbeddingValidationError,
)
from h0_runtime import H0InfrastructureError  # noqa: E402


def _unit_vector() -> list[float]:
    return [1.0, *([0.0] * (EMBEDDING_DIMENSION - 1))]


def _response(request: httpx.Request, vectors: list[list[float]]) -> httpx.Response:
    payload = {
        "object": "list",
        "model": "qwen3-embedding-0.6b",
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": len(vectors), "total_tokens": len(vectors)},
    }
    return httpx.Response(
        200,
        request=request,
        content=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


class H0EmbeddingAdapterTests(IsolatedAsyncioTestCase):
    binding = {
        "base_url": "http://embedding.invalid/v1",
        "served_model_id": "qwen3-embedding-0.6b",
        "vllm_version": "0.26.0",
        "dimension": 1024,
        "normalization": "l2",
    }
    credentials = {
        "base_url": "http://embedding.invalid/v1/",
        "model": "qwen3-embedding-0.6b",
        "api_key": "TEST-SECRET-KEY",
    }

    def _adapter(self, handler) -> H0EmbeddingAdapter:
        return H0EmbeddingAdapter(
            binding=self.binding,
            credentials=self.credentials,
            transport=httpx.MockTransport(handler),
        )

    async def test_client_disables_sdk_retry_proxy_inheritance_and_redirects(self):
        adapter = self._adapter(
            lambda request: _response(request, [_unit_vector()])
        )
        try:
            self.assertEqual(adapter._client.max_retries, 0)
            self.assertFalse(adapter._http_client._trust_env)
            self.assertFalse(adapter._http_client.follow_redirects)
            self.assertEqual(str(adapter._client.base_url), "http://embedding.invalid/v1/")
        finally:
            await adapter.close()

    async def test_binding_rejects_endpoint_model_dimension_or_normalization_drift(self):
        cases = (
            ({"base_url": "http://embedding.invalid/v1/extra"}, self.credentials),
            (self.binding, self.credentials | {"base_url": "http://other.invalid/v1"}),
            (self.binding, self.credentials | {"model": "other-model"}),
            (self.binding | {"dimension": 768}, self.credentials),
            (self.binding | {"normalization": "none"}, self.credentials),
        )
        for binding, credentials in cases:
            with self.subTest(binding=binding, credentials=credentials):
                with self.assertRaises(H0EmbeddingValidationError):
                    H0EmbeddingAdapter(
                        binding=binding,
                        credentials=credentials,
                        transport=httpx.MockTransport(
                            lambda request: _response(request, [_unit_vector()])
                        ),
                    )

    async def test_readiness_is_metadata_only_with_no_embedding_or_sensitive_evidence(self):
        requests: list[tuple[str, str]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                request.headers.get("Authorization"),
                "Bearer TEST-SECRET-KEY",
            )
            requests.append((request.method, request.url.path))
            if request.url.path == "/version":
                return httpx.Response(200, request=request, json={"version": "0.26.0"})
            if request.url.path == "/v1/models":
                return httpx.Response(
                    200,
                    request=request,
                    json={"data": [{"id": "qwen3-embedding-0.6b"}]},
                )
            if request.url.path == "/health":
                return httpx.Response(200, request=request)
            self.fail(f"unexpected readiness request: {request.url.path}")

        adapter = self._adapter(handler)
        try:
            readiness = await adapter.readiness()
            self.assertEqual(
                requests,
                [
                    ("GET", "/version"),
                    ("GET", "/v1/models"),
                    ("GET", "/health"),
                ],
            )
            self.assertEqual(readiness["request_count"], 3)
            self.assertEqual(readiness["embedding_request_count"], 0)
            self.assertEqual(readiness["served_model_id"], self.binding["served_model_id"])
            self.assertEqual(readiness["vllm_version"], "0.26.0")
            self.assertEqual(readiness["llm_request_count"], 0)
            self.assertEqual(readiness["warmup_performed"], False)
            persisted = json.dumps(
                {"readiness": readiness, "events": adapter.safe_evidence()},
                sort_keys=True,
            )
            self.assertNotIn("TEST-SECRET-KEY", persisted)
            self.assertNotIn("Authorization", persisted)
            with self.assertRaises(H0EmbeddingValidationError):
                await adapter.readiness()
            self.assertEqual(len(requests), 3)
        finally:
            await adapter.close()

    async def test_readiness_rejects_vllm_or_model_drift_before_workload_embedding(self):
        cases = (
            ({"version": "0.25.0"}, {"data": [{"id": "qwen3-embedding-0.6b"}]}),
            ({"version": "0.26.0"}, {"data": [{"id": "other-model"}]}),
        )
        for version, models in cases:
            calls: list[str] = []

            async def handler(request: httpx.Request) -> httpx.Response:
                calls.append(request.url.path)
                if request.url.path == "/version":
                    return httpx.Response(200, request=request, json=version)
                if request.url.path == "/v1/models":
                    return httpx.Response(200, request=request, json=models)
                return httpx.Response(200, request=request)

            with self.subTest(version=version, models=models):
                adapter = self._adapter(handler)
                try:
                    with self.assertRaises(H0EmbeddingValidationError):
                        await adapter.readiness()
                    self.assertNotIn("/v1/embeddings", calls)
                    self.assertEqual(adapter.safe_evidence(), [])
                finally:
                    await adapter.close()

    async def test_create_and_batch_validate_vectors_and_persist_only_safe_projections(self):
        request_count = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            payload = json.loads(request.content)
            inputs = payload["input"] if isinstance(payload["input"], list) else [payload["input"]]
            return _response(request, [_unit_vector() for _ in inputs])

        adapter = self._adapter(handler)
        try:
            vector = await adapter.create("private single text")
            vectors = await adapter.create_batch(["private batch one", "private batch two"])
            self.assertEqual(len(vector), 1024)
            self.assertEqual([len(value) for value in vectors], [1024, 1024])
            self.assertEqual(request_count, 2)
            events = adapter.safe_evidence()
            self.assertEqual(len(events), 2)
            allowed = {
                "request_count",
                "input_count",
                "input_utf8_byte_count",
                "input_sha256",
                "embedding_count",
                "embedding_sha256",
                "dimension",
                "dimensions",
                "l2_norm",
                "l2_norms",
                "http_attempt_count",
                "llm_request_count",
            }
            self.assertTrue(all(set(event) <= allowed for event in events))
            persisted = json.dumps(events, sort_keys=True)
            self.assertNotIn("private", persisted)
            self.assertNotIn("TEST-SECRET-KEY", persisted)
            self.assertNotIn("1.0, 0.0", persisted)
        finally:
            await adapter.close()

    async def test_rejects_wrong_dimension_nonfinite_or_nonunit_vectors(self):
        vectors = (
            [1.0],
            [math.nan, *([0.0] * (EMBEDDING_DIMENSION - 1))],
            [2.0, *([0.0] * (EMBEDDING_DIMENSION - 1))],
        )
        for vector in vectors:
            with self.subTest(kind=(len(vector), vector[0])):
                adapter = self._adapter(
                    lambda request, value=vector: _response(request, [value])
                )
                try:
                    with self.assertRaises(H0EmbeddingValidationError):
                        await adapter.create("validation input")
                finally:
                    await adapter.close()

    async def test_connect_timeout_429_and_5xx_are_infrastructure_stop_without_retry(self):
        for scenario in ("connect", "timeout", 429, 500, 503):
            calls = 0

            async def handler(request: httpx.Request, selected=scenario) -> httpx.Response:
                nonlocal calls
                calls += 1
                if selected == "connect":
                    raise httpx.ConnectError("private connect detail", request=request)
                if selected == "timeout":
                    raise httpx.ReadTimeout("private timeout detail", request=request)
                return httpx.Response(
                    selected,
                    request=request,
                    json={"error": {"message": "private server detail"}},
                )

            with self.subTest(scenario=scenario):
                adapter = self._adapter(handler)
                try:
                    with self.assertRaisesRegex(
                        H0InfrastructureError,
                        "embedding_unreachable: stop_and_report",
                    ) as raised:
                        await adapter.create("infrastructure input")
                    self.assertNotIn("private", str(raised.exception))
                finally:
                    await adapter.close()
                self.assertEqual(calls, 1)

    async def test_non_infrastructure_http_error_is_validation_failure(self):
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                401,
                request=request,
                json={"error": {"message": "private authentication detail"}},
            )

        adapter = self._adapter(handler)
        try:
            with self.assertRaises(H0EmbeddingValidationError) as raised:
                await adapter.create("authentication input")
            self.assertNotIn("private", str(raised.exception))
            self.assertEqual(calls, 1)
        finally:
            await adapter.close()

    async def test_close_is_idempotent_and_best_effort_for_both_resources(self):
        adapter = self._adapter(
            lambda request: _response(request, [_unit_vector()])
        )
        await adapter.close()
        await adapter.close()
        self.assertTrue(adapter._client.is_closed())
        self.assertTrue(adapter._http_client.is_closed)

        failing_openai_close = AsyncMock(side_effect=RuntimeError("private close"))
        http_close = AsyncMock()
        adapter._closed = False
        adapter._client = SimpleNamespace(close=failing_openai_close)
        adapter._http_client = SimpleNamespace(aclose=http_close)
        await adapter.close()
        failing_openai_close.assert_awaited_once()
        http_close.assert_awaited_once()


if __name__ == "__main__":
    import unittest

    unittest.main()
