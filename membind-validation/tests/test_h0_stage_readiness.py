"""Offline orchestration contracts for H0-B/C full-stack readiness.

Every dependency is a fake.  The suite proves ordering, immediate durable
event delivery, one-shot component use, cleanup, and stop-and-report behavior
without reading configuration or contacting any service.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_runtime import (  # noqa: E402
    H0InfrastructureError,
    H0ManifestError,
    H0StateGateError,
)
from h0_stage_readiness import run_h0_stage_readiness  # noqa: E402


class _EmbeddingReadiness:
    def __init__(
        self,
        events: list[str],
        *,
        failure: Exception | None = None,
        result: dict | None = None,
    ) -> None:
        self.events = events
        self.failure = failure
        self.result = result or {
            "event": "embedding_metadata_readiness",
            "request_count": 3,
            "http_attempt_count": 3,
            "embedding_request_count": 0,
            "llm_request_count": 0,
            "served_model_id": "qwen3-embedding-0.6b",
            "vllm_version": "0.26.0",
            "warmup_performed": False,
        }
        self.readiness = AsyncMock(side_effect=self._readiness)
        self.close = AsyncMock(side_effect=self._close)
        self.create = AsyncMock(side_effect=AssertionError("embedding warm-up forbidden"))

    async def _readiness(self):
        self.events.append("embedding-readiness")
        if self.failure is not None:
            raise self.failure
        return dict(self.result)

    async def _close(self):
        self.events.append("embedding-close")


class _Neo4jReadiness:
    def __init__(
        self,
        events: list[str],
        *,
        failure: Exception | None = None,
        result: dict | None = None,
    ) -> None:
        self.events = events
        self.failure = failure
        self.result = result or {
            "attempt_id": "h0-q1-b-attempt-003",
            "candidate": "Q1",
            "phase": "H0-B",
            "driver_construct_attempt_count": 1,
            "verify_connectivity_call_count": 1,
            "cypher_call_count": 0,
            "close_call_count": 1,
            "readiness_code": "pass",
            "failure_code": None,
            "uri_sha256": "a" * 64,
        }
        self.readiness = AsyncMock(side_effect=self._readiness)
        self.close = AsyncMock(side_effect=self._close)

    async def _readiness(self):
        self.events.append("neo4j-readiness")
        if self.failure is not None:
            raise self.failure
        return dict(self.result)

    async def _close(self):
        self.events.append("neo4j-close")


class H0StageReadinessTests(IsolatedAsyncioTestCase):
    attempt_id = "h0-q1-b-attempt-003"
    authorization = {
        "candidate_id": "Q1",
        "phase": "H0-B",
        "resolved_manifest_index_sha256": "1" * 64,
    }
    embedding_binding = {
        "base_url": "http://embedding.invalid/v1/",
        "served_model_id": "qwen3-embedding-0.6b",
        "vllm_version": "0.26.0",
        "dimension": 1024,
        "normalization": "l2",
    }
    embedding_credentials = {
        "base_url": "http://embedding.invalid/v1/",
        "model": "qwen3-embedding-0.6b",
        "api_key": "TEST-EMBEDDING-SECRET",
    }
    neo4j_binding = {"uri": "bolt://localhost:7687", "user": "neo4j"}
    neo4j_credentials = {
        "uri": "bolt://localhost:7687",
        "user": "neo4j",
        "password": "TEST-NEO4J-SECRET",
    }

    def _construction_result(self, **changes) -> dict:
        return {
            "status": "ready",
            "authorized_candidate_execution_ready": True,
            "generation_requests": 0,
            "checks": [],
            **changes,
        }

    def _kwargs(
        self,
        *,
        events: list[str],
        construction_runner=None,
        embedding=None,
        neo4j=None,
        authorization_checker=None,
    ) -> tuple[dict, list[dict], Mock, Mock]:
        persisted: list[dict] = []
        authorization_calls = 0

        def authorize(**_kwargs):
            nonlocal authorization_calls
            authorization_calls += 1
            events.append(f"authorization-{authorization_calls}")
            return dict(self.authorization)

        async def construction(**kwargs):
            events.append("construction-readiness")
            kwargs["authorization_checker"](
                state_path=kwargs["state_path"],
                candidate_id=kwargs["candidate_id"],
                phase=kwargs["phase"],
            )
            for check in ("vllm_version", "served_model", "health"):
                kwargs["progress_sink"](
                    {
                        "check": check,
                        "qualified": True,
                        "failure_code": None,
                        "generation_requests": 0,
                    }
                )
            return self._construction_result()

        selected_embedding = embedding or _EmbeddingReadiness(events)
        selected_neo4j = neo4j or _Neo4jReadiness(events)

        def embedding_factory(**kwargs):
            events.append("embedding-constructed")
            self.assertEqual(kwargs["binding"], self.embedding_binding)
            self.assertEqual(kwargs["credentials"], self.embedding_credentials)
            return selected_embedding

        def neo4j_factory(**kwargs):
            events.append("neo4j-constructed")
            self.assertEqual(kwargs["binding"], self.neo4j_binding)
            self.assertEqual(kwargs["credentials"], self.neo4j_credentials)
            self.assertEqual(kwargs["attempt_id"], self.attempt_id)
            self.assertEqual(kwargs["candidate"], "Q1")
            self.assertEqual(kwargs["phase"], "H0-B")
            return selected_neo4j

        def persist(event):
            persisted.append(dict(event))
            events.append(f"persist:{event['check']}")

        embedding_factory_mock = Mock(side_effect=embedding_factory)
        neo4j_factory_mock = Mock(side_effect=neo4j_factory)
        kwargs = {
            "state_path": ROOT / "offline-state.json",
            "stage_attempt_id": self.attempt_id,
            "candidate_id": "Q1",
            "phase": "H0-B",
            "construction_credential_loader": Mock(
                return_value={
                    "base_url": "http://construction.invalid/v1/",
                    "api_key": "TEST-CONSTRUCTION-SECRET",
                }
            ),
            "resolved_identity_loader": Mock(return_value={"safe": "identity"}),
            "embedding_binding": self.embedding_binding,
            "embedding_credentials": self.embedding_credentials,
            "neo4j_binding": self.neo4j_binding,
            "neo4j_credentials": self.neo4j_credentials,
            "authorization_checker": authorization_checker or authorize,
            "construction_readiness_runner": construction_runner or construction,
            "embedding_adapter_factory": embedding_factory_mock,
            "neo4j_readiness_factory": neo4j_factory_mock,
            "progress_sink": persist,
        }
        return kwargs, persisted, embedding_factory_mock, neo4j_factory_mock

    async def test_success_is_ordered_persisted_one_shot_and_generation_free(self):
        events: list[str] = []
        kwargs, persisted, embedding_factory, neo4j_factory = self._kwargs(events=events)

        result = await run_h0_stage_readiness(**kwargs)

        self.assertEqual(
            events,
            [
                "construction-readiness",
                "authorization-1",
                "persist:vllm_version",
                "persist:served_model",
                "persist:health",
                "persist:construction_ready",
                "embedding-constructed",
                "embedding-readiness",
                "persist:embedding_ready",
                "embedding-close",
                "neo4j-constructed",
                "neo4j-readiness",
                "persist:neo4j_ready",
                "neo4j-close",
                "authorization-2",
                "persist:authorization_recheck",
            ],
        )
        embedding_factory.assert_called_once()
        neo4j_factory.assert_called_once()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["generation_requests"], 0)
        self.assertEqual(result["embedding_request_count"], 0)
        self.assertEqual(result["per_history_warmup_count"], 0)
        self.assertEqual(result["construction_readiness_count"], 1)
        self.assertEqual(result["embedding_readiness_count"], 1)
        self.assertEqual(result["neo4j_readiness_count"], 1)
        self.assertEqual(len(persisted), 7)
        self.assertTrue(all(event["stage_attempt_id"] == self.attempt_id for event in persisted))
        encoded = json.dumps({"result": result, "events": persisted}, sort_keys=True)
        self.assertNotIn("SECRET", encoded)
        self.assertNotIn("construction.invalid", encoded)
        self.assertNotIn("embedding.invalid", encoded)
        self.assertNotIn("bolt://", encoded)

    async def test_infrastructure_failures_propagate_unchanged_and_stop_later_components(self):
        for component in ("construction", "embedding", "neo4j"):
            with self.subTest(component=component):
                events: list[str] = []
                failure = H0InfrastructureError(
                    f"{component}_unreachable: stop_and_report"
                )

                async def construction(**kwargs):
                    events.append("construction-readiness")
                    kwargs["authorization_checker"](
                        state_path=kwargs["state_path"],
                        candidate_id=kwargs["candidate_id"],
                        phase=kwargs["phase"],
                    )
                    if component == "construction":
                        raise failure
                    return self._construction_result()

                embedding = _EmbeddingReadiness(
                    events,
                    failure=failure if component == "embedding" else None,
                )
                neo4j = _Neo4jReadiness(
                    events,
                    failure=failure if component == "neo4j" else None,
                )
                kwargs, persisted, embedding_factory, neo4j_factory = self._kwargs(
                    events=events,
                    construction_runner=construction,
                    embedding=embedding,
                    neo4j=neo4j,
                )

                with self.assertRaises(H0InfrastructureError) as raised:
                    await run_h0_stage_readiness(**kwargs)

                self.assertIs(raised.exception, failure)
                if component == "construction":
                    embedding_factory.assert_not_called()
                    neo4j_factory.assert_not_called()
                elif component == "embedding":
                    embedding.readiness.assert_awaited_once()
                    embedding.close.assert_awaited_once()
                    neo4j_factory.assert_not_called()
                    self.assertEqual(
                        persisted[-1]["check"],
                        "embedding_readiness_failure",
                    )
                    self.assertEqual(
                        persisted[-1]["failure_code"],
                        "embedding_unreachable",
                    )
                else:
                    neo4j.readiness.assert_awaited_once()
                    neo4j.close.assert_awaited_once()
                    self.assertEqual(
                        persisted[-1]["check"],
                        "neo4j_readiness_failure",
                    )
                    self.assertEqual(
                        persisted[-1]["failure_code"],
                        "neo4j_unreachable",
                    )
                self.assertNotIn("authorization-2", events)

    async def test_contract_violations_stop_before_next_component_and_still_close(self):
        events: list[str] = []

        async def unsafe_construction(**kwargs):
            kwargs["authorization_checker"](
                state_path=kwargs["state_path"],
                candidate_id=kwargs["candidate_id"],
                phase=kwargs["phase"],
            )
            return self._construction_result(generation_requests=1)

        kwargs, _persisted, embedding_factory, _ = self._kwargs(
            events=events,
            construction_runner=unsafe_construction,
        )
        with self.assertRaises(H0ManifestError):
            await run_h0_stage_readiness(**kwargs)
        embedding_factory.assert_not_called()

        events = []
        unsafe_embedding = _EmbeddingReadiness(
            events,
            result={
                "event": "embedding_metadata_readiness",
                "embedding_request_count": 1,
                "llm_request_count": 0,
                "warmup_performed": True,
            },
        )
        kwargs, _persisted, _, neo4j_factory = self._kwargs(
            events=events,
            embedding=unsafe_embedding,
        )
        with self.assertRaises(H0ManifestError):
            await run_h0_stage_readiness(**kwargs)
        unsafe_embedding.close.assert_awaited_once()
        neo4j_factory.assert_not_called()

        events = []
        unsafe_neo4j = _Neo4jReadiness(
            events,
            result={
                "readiness_code": "pass",
                "verify_connectivity_call_count": 1,
                "cypher_call_count": 1,
            },
        )
        kwargs, _persisted, _, _ = self._kwargs(events=events, neo4j=unsafe_neo4j)
        with self.assertRaises(H0ManifestError):
            await run_h0_stage_readiness(**kwargs)
        unsafe_neo4j.close.assert_awaited_once()
        self.assertNotIn("authorization-2", events)

    async def test_final_authorization_drift_fails_without_success_checkpoint(self):
        events: list[str] = []
        calls = 0

        def drifting_authorization(**_kwargs):
            nonlocal calls
            calls += 1
            events.append(f"authorization-{calls}")
            if calls == 1:
                return dict(self.authorization)
            return self.authorization | {"phase": "H0-C"}

        kwargs, persisted, _, _ = self._kwargs(
            events=events,
            authorization_checker=drifting_authorization,
        )

        with self.assertRaises(H0StateGateError):
            await run_h0_stage_readiness(**kwargs)

        self.assertEqual(calls, 2)
        self.assertNotIn("authorization_recheck", [event["check"] for event in persisted])

    async def test_construction_runner_must_invoke_exactly_one_initial_gate(self):
        events: list[str] = []

        async def skips_gate(**kwargs):
            kwargs["progress_sink"](
                {
                    "check": "premature_transport",
                    "qualified": True,
                    "failure_code": None,
                }
            )
            return self._construction_result()

        kwargs, persisted, embedding_factory, _ = self._kwargs(
            events=events,
            construction_runner=skips_gate,
        )
        with self.assertRaises(H0StateGateError):
            await run_h0_stage_readiness(**kwargs)
        self.assertEqual(persisted, [])
        embedding_factory.assert_not_called()

    async def test_cleanup_failure_does_not_mask_infrastructure_stop(self):
        for component in ("embedding", "neo4j"):
            with self.subTest(component=component):
                events: list[str] = []
                infrastructure = H0InfrastructureError(
                    f"{component}_unreachable: stop_and_report"
                )
                embedding = _EmbeddingReadiness(
                    events,
                    failure=infrastructure if component == "embedding" else None,
                )
                neo4j = _Neo4jReadiness(
                    events,
                    failure=infrastructure if component == "neo4j" else None,
                )
                resource = embedding if component == "embedding" else neo4j
                resource.close = AsyncMock(
                    side_effect=RuntimeError("private cleanup detail")
                )
                kwargs, _persisted, _, _ = self._kwargs(
                    events=events,
                    embedding=embedding,
                    neo4j=neo4j,
                )

                with self.assertRaises(H0InfrastructureError) as raised:
                    await run_h0_stage_readiness(**kwargs)

                self.assertIs(raised.exception, infrastructure)
                resource.close.assert_awaited_once()
