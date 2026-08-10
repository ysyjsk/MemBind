"""Offline contracts for the H0-B/C Neo4j readiness adapter.

The fake drivers below never open a socket.  These tests prove that the
adapter performs only one connectivity check, closes its driver, and emits a
strictly sanitized evidence projection.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from neo4j.exceptions import ServiceUnavailable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_neo4j import (  # noqa: E402
    H0Neo4jReadiness,
    H0Neo4jValidationError,
)
from h0_runtime import H0InfrastructureError  # noqa: E402


class _FakeDriver:
    def __init__(self, *, failure: Exception | None = None, close_failure=False):
        self.verify_connectivity = AsyncMock(side_effect=failure)
        self.close = AsyncMock(
            side_effect=RuntimeError("private close detail") if close_failure else None
        )
        self.session = Mock(side_effect=AssertionError("Cypher/session access forbidden"))


class H0Neo4jReadinessTests(IsolatedAsyncioTestCase):
    binding = {
        "uri": "neo4j://127.0.0.1:7687",
        "user": "neo4j",
    }
    credentials = {
        "uri": "neo4j://127.0.0.1:7687/",
        "user": "neo4j",
        "password": "TEST-NEO4J-SECRET",
    }
    identity = {
        "attempt_id": "h0-q1-b-attempt-002",
        "candidate": "Q1",
        "phase": "H0-B",
    }

    def _adapter(self, driver_factory) -> H0Neo4jReadiness:
        return H0Neo4jReadiness(
            binding=self.binding,
            credentials=self.credentials,
            driver_factory=driver_factory,
            **self.identity,
        )

    async def test_binding_and_credentials_drift_fail_before_driver_construction(self):
        cases = (
            (self.binding | {"uri": "neo4j://127.0.0.1:7688"}, self.credentials),
            (self.binding, self.credentials | {"uri": "neo4j://localhost:7687"}),
            (self.binding, self.credentials | {"user": "other"}),
            (self.binding, self.credentials | {"password": ""}),
            (self.binding | {"unexpected": "parameter"}, self.credentials),
            (
                self.binding,
                self.credentials
                | {"uri": "neo4j://neo4j:secret@127.0.0.1:7687"},
            ),
        )
        for binding, credentials in cases:
            with self.subTest(binding=binding, credentials=credentials):
                factory = Mock()
                with self.assertRaises(H0Neo4jValidationError):
                    H0Neo4jReadiness(
                        binding=binding,
                        credentials=credentials,
                        driver_factory=factory,
                        **self.identity,
                    )
                factory.assert_not_called()

    async def test_readiness_verifies_once_closes_and_emits_only_safe_evidence(self):
        driver = _FakeDriver()
        factory = Mock(return_value=driver)
        adapter = self._adapter(factory)

        result = await adapter.readiness()

        factory.assert_called_once_with(
            "neo4j://127.0.0.1:7687",
            auth=("neo4j", "TEST-NEO4J-SECRET"),
        )
        driver.verify_connectivity.assert_awaited_once_with()
        driver.close.assert_awaited_once_with()
        driver.session.assert_not_called()
        self.assertEqual(
            result,
            {
                "uri_sha256": hashlib.sha256(
                    b"neo4j://127.0.0.1:7687"
                ).hexdigest(),
                **self.identity,
                "driver_construct_attempt_count": 1,
                "verify_connectivity_call_count": 1,
                "cypher_call_count": 0,
                "close_call_count": 1,
                "readiness_code": "pass",
                "failure_code": None,
            },
        )
        self.assertEqual(adapter.safe_evidence(), [result])
        persisted = json.dumps(adapter.safe_evidence(), sort_keys=True)
        for secret in (
            "neo4j://127.0.0.1:7687",
            "TEST-NEO4J-SECRET",
            '"user"',
            '"password"',
        ):
            self.assertNotIn(secret, persisted)

        with self.assertRaises(H0Neo4jValidationError):
            await adapter.readiness()
        factory.assert_called_once()
        driver.verify_connectivity.assert_awaited_once()
        driver.close.assert_awaited_once()

    async def test_infrastructure_failures_are_sanitized_recorded_and_not_retried(self):
        failures = (
            ServiceUnavailable("private service detail"),
            TimeoutError("private timeout detail"),
            ConnectionError("private connection detail"),
            OSError("private socket detail"),
        )
        for failure in failures:
            with self.subTest(kind=type(failure).__name__):
                driver = _FakeDriver(failure=failure)
                factory = Mock(return_value=driver)
                adapter = self._adapter(factory)

                with self.assertRaisesRegex(
                    H0InfrastructureError,
                    "^neo4j_unreachable: stop_and_report$",
                ) as raised:
                    await adapter.readiness()

                self.assertNotIn("private", str(raised.exception))
                factory.assert_called_once()
                driver.verify_connectivity.assert_awaited_once()
                driver.close.assert_awaited_once()
                evidence = adapter.safe_evidence()
                self.assertEqual(len(evidence), 1)
                self.assertEqual(evidence[0]["readiness_code"], "failure")
                self.assertEqual(evidence[0]["failure_code"], "neo4j_unreachable")
                self.assertNotIn("private", json.dumps(evidence, sort_keys=True))

    async def test_unknown_verify_failure_is_sanitized_validation_failure(self):
        driver = _FakeDriver(failure=ValueError("private response detail"))
        adapter = self._adapter(Mock(return_value=driver))

        with self.assertRaises(H0Neo4jValidationError) as raised:
            await adapter.readiness()

        self.assertNotIn("private", str(raised.exception))
        driver.verify_connectivity.assert_awaited_once()
        driver.close.assert_awaited_once()
        self.assertEqual(
            adapter.safe_evidence()[0]["failure_code"],
            "neo4j_readiness_contract_failure",
        )

    async def test_driver_construction_failure_is_sanitized_and_close_is_best_effort(self):
        factory = Mock(side_effect=ServiceUnavailable("private construction detail"))
        adapter = self._adapter(factory)

        with self.assertRaisesRegex(
            H0InfrastructureError,
            "^neo4j_unreachable: stop_and_report$",
        ):
            await adapter.readiness()
        self.assertEqual(
            adapter.safe_evidence()[0]
            | {"uri_sha256": "redacted", **self.identity},
            {
                "uri_sha256": "redacted",
                **self.identity,
                "driver_construct_attempt_count": 1,
                "verify_connectivity_call_count": 0,
                "cypher_call_count": 0,
                "close_call_count": 0,
                "readiness_code": "failure",
                "failure_code": "neo4j_unreachable",
            },
        )

        driver = _FakeDriver(close_failure=True)
        passing = self._adapter(Mock(return_value=driver))
        result = await passing.readiness()
        self.assertEqual(result["readiness_code"], "pass")
        self.assertEqual(result["close_call_count"], 1)
        driver.close.assert_awaited_once()

    async def test_safe_evidence_is_a_defensive_copy(self):
        adapter = self._adapter(Mock(return_value=_FakeDriver()))
        await adapter.readiness()
        evidence = adapter.safe_evidence()
        evidence[0]["failure_code"] = "mutated"
        self.assertIsNone(adapter.safe_evidence()[0]["failure_code"])
