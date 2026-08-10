"""Offline TDD contracts for the dedicated Protocol v1.3 H0 live entry point."""

from __future__ import annotations

import sys
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_artifacts import build_h0_semantic_guardrail_manifest  # noqa: E402
from h0_live_runner import (  # noqa: E402
    DEFAULT_ARTIFACTS_ROOT,
    H0AClientFactory,
    _load_primary_h0_record,
    execute_h0_a_live,
)
from h0_phase_runner import H0SemanticEvidenceCollector, run_h0_a  # noqa: E402
from h0_runtime import (  # noqa: E402
    H0AttemptLedger,
    H0CandidateConfig,
    H0CheckpointStore,
    H0InfrastructureError,
    H0SemanticError,
    H0StateGateError,
    canonical_json_sha256,
)


def _authorization() -> dict[str, object]:
    return {
        "candidate_id": "Q1",
        "phase": "H0-A",
        "resolved_manifest_index_sha256": "1" * 64,
        "resolved_candidate_manifest_sha256": "2" * 64,
        "resolved_shared_base_manifest_sha256": "3" * 64,
    }


def _definition() -> SimpleNamespace:
    return SimpleNamespace(
        identity={
            "candidate_id": "Q1",
            "phase": "H0-A",
            "base_url": "http://offline.invalid/v1/",
            "served_model_id": "qwen3-32b-fp8",
            "vllm_version": "0.26.0",
            "context_limit": 40960,
            "resolved_manifest_index_sha256": "1" * 64,
            "resolved_candidate_manifest_sha256": "2" * 64,
            "resolved_shared_base_manifest_sha256": "3" * 64,
        },
        candidate=SimpleNamespace(candidate_id="Q1"),
        semantic_guardrail={"schema_version": "offline-guardrail"},
        definition_sha256="4" * 64,
    )


class H0LiveRunnerOrderingTests(IsolatedAsyncioTestCase):
    async def test_repair_state_rejects_wrong_attempt_before_definition_or_checkpoint(self):
        authorization = _authorization() | {
            "authorized_stage_attempt_id": "expected-replacement",
            "repair_admission": {
                "schema_version": "membind.h0.repair-admission.v1",
                "replacement_attempt_id": "expected-replacement",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with self.assertRaises(H0StateGateError):
                await execute_h0_a_live(
                    root=root,
                    state_path=root / "state.json",
                    artifacts_root=root / "artifacts",
                    stage_attempt_id="wrong-replacement",
                    authorization_checker=Mock(return_value=authorization),
                    runtime_definition_loader=Mock(
                        side_effect=AssertionError("definition must not load")
                    ),
                    checkpoint_store_factory=Mock(
                        side_effect=AssertionError("checkpoint must not exist")
                    ),
                    credential_loader=Mock(side_effect=AssertionError("env")),
                    readiness_runner=Mock(side_effect=AssertionError("readiness")),
                )

    async def test_exact_state_repair_admission_is_forwarded_to_checkpoint_gate(self):
        repair = {
            "schema_version": "membind.h0.repair-admission.v1",
            "replacement_attempt_id": "replacement-attempt",
        }
        authorization = _authorization() | {"repair_admission": repair}

        def checkpoint_gate(**kwargs):
            self.assertEqual(kwargs["repair_admission"], repair)
            raise H0StateGateError("checkpoint gate observed repair admission")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with self.assertRaisesRegex(H0StateGateError, "observed repair"):
                await execute_h0_a_live(
                    root=root,
                    state_path=root / "state.json",
                    artifacts_root=root / "artifacts",
                    stage_attempt_id="replacement-attempt",
                    authorization_checker=Mock(return_value=authorization),
                    runtime_definition_loader=Mock(return_value=_definition()),
                    checkpoint_store_factory=checkpoint_gate,
                    credential_loader=Mock(side_effect=AssertionError("env")),
                    readiness_runner=Mock(side_effect=AssertionError("readiness")),
                )

    async def test_live_checkpoint_namespace_does_not_overlap_offline_manifests(self):
        offline_manifest_root = (ROOT / "artifacts/h0").resolve()
        live_checkpoint_root = (DEFAULT_ARTIFACTS_ROOT / "h0").resolve()
        self.assertNotEqual(live_checkpoint_root, offline_manifest_root)
        self.assertNotIn(offline_manifest_root, live_checkpoint_root.parents)

    async def test_denied_gate_precedes_manifest_artifact_env_and_client(self):
        order: list[str] = []

        def deny(**_kwargs):
            order.append("gate")
            raise H0StateGateError("offline denial")

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            with self.assertRaises(H0StateGateError):
                await execute_h0_a_live(
                    root=Path(tmp),
                    state_path=Path(tmp) / "state.json",
                    artifacts_root=artifacts,
                    stage_attempt_id="denied-attempt",
                    authorization_checker=deny,
                    runtime_definition_loader=Mock(
                        side_effect=AssertionError("manifest must not load")
                    ),
                    record_loader=Mock(
                        side_effect=AssertionError("dataset must not load")
                    ),
                    credential_loader=Mock(
                        side_effect=AssertionError("env must not load")
                    ),
                    checkpoint_store_factory=Mock(
                        side_effect=AssertionError("artifact must not exist")
                    ),
                    readiness_runner=Mock(
                        side_effect=AssertionError("readiness must not run")
                    ),
                    client_factory_builder=Mock(
                        side_effect=AssertionError("client must not exist")
                    ),
                    phase_runner=Mock(
                        side_effect=AssertionError("generation must not run")
                    ),
                )
            self.assertEqual(order, ["gate"])
            self.assertFalse(artifacts.exists())

    async def test_readiness_precedes_client_and_success_is_terminal_and_durable(self):
        order: list[str] = []
        definition = _definition()

        def authorize(**_kwargs):
            order.append("gate")
            return _authorization()

        def load_definition(_authorization, *, root):
            self.assertEqual(Path(root), Path(root).resolve())
            order.append("definition")
            return definition

        def load_credentials():
            order.append("env")
            return {"base_url": definition.identity["base_url"], "api_key": "SECRET"}

        async def readiness(**kwargs):
            order.append("readiness")
            kwargs["authorization_checker"](
                state_path=kwargs["state_path"], candidate_id="Q1", phase="H0-A"
            )
            kwargs["resolved_identity_loader"](_authorization())
            kwargs["credential_loader"]()
            for check in ("vllm_version", "served_model", "health"):
                kwargs["progress_sink"](
                    {
                        "schema_version": "membind.h0.readiness-event.v1",
                        "check": check,
                        "qualified": True,
                        "candidate_advance_allowed": False,
                    }
                )
            return {
                "schema_version": "membind.h0.readiness-preflight.v1",
                "status": "ready",
                "authorized_candidate_execution_ready": True,
                "generation_requests": 0,
            }

        client_factory = SimpleNamespace(
            safe_runtime_evidence=lambda: {"tokenize_events": [], "wire_events": []}
        )

        def build_client_factory(**_kwargs):
            self.assertIn("readiness", order)
            order.append("client_factory")
            return client_factory

        async def run_phase(**kwargs):
            order.append("phase")
            self.assertIs(kwargs["client_factory"], client_factory)
            for index in range(3):
                await kwargs["trial_checkpoint"](
                    {
                        "schema_version": "membind.h0.phase-checkpoint.v1",
                        "stage_attempt_id": kwargs["stage_attempt_id"],
                        "phase": "H0-A",
                        "repeated_trial_index": index,
                        "qualified": True,
                    }
                )
            return {
                "schema_version": "membind.h0.phase-result.v1",
                "stage_attempt_id": kwargs["stage_attempt_id"],
                "phase": "H0-A",
                "qualified": True,
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            result = await execute_h0_a_live(
                root=root,
                state_path=root / "state.json",
                artifacts_root=root / "artifacts",
                stage_attempt_id="success-attempt",
                authorization_checker=authorize,
                runtime_definition_loader=load_definition,
                record_loader=Mock(return_value={"question_id": "07741c45"}),
                credential_loader=load_credentials,
                readiness_runner=readiness,
                client_factory_builder=build_client_factory,
                phase_runner=run_phase,
                progress_sink=lambda event: order.append(str(event["status"])),
            )

            self.assertEqual(result["status"], "stage_complete")
            self.assertEqual(result["phase_result"]["qualified"], True)
            self.assertEqual(result["runtime_definition_sha256"], "4" * 64)
            reopened = H0CheckpointStore.open_existing(
                root / "artifacts", "success-attempt"
            )
            self.assertEqual(reopened.index["status"], "stage_complete")
            self.assertEqual(
                reopened.index["terminal_result_sha256"],
                canonical_json_sha256(result["phase_result"]),
            )
            self.assertEqual(
                [entry["segment_kind"] for entry in reopened.index["segments"]],
                [
                    "readiness_check",
                    "readiness_check",
                    "readiness_check",
                    "readiness_result",
                    "logical_trial",
                    "logical_trial",
                    "logical_trial",
                    "stage_result",
                ],
            )
            self.assertLess(order.index("readiness"), order.index("client_factory"))
            self.assertLess(order.index("env"), order.index("client_factory"))

    async def test_authorization_revoked_after_readiness_prevents_generation_client(self):
        definition = _definition()
        authorization_checks = Mock(
            side_effect=[
                _authorization(),
                H0StateGateError("authorization revoked after readiness"),
            ]
        )

        async def readiness(**_kwargs):
            return {
                "schema_version": "membind.h0.readiness-preflight.v1",
                "status": "ready",
                "authorized_candidate_execution_ready": True,
                "generation_requests": 0,
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            artifacts = root / "artifacts"
            with self.assertRaises(H0StateGateError):
                await execute_h0_a_live(
                    root=root,
                    state_path=root / "state.json",
                    artifacts_root=artifacts,
                    stage_attempt_id="revoked-attempt",
                    authorization_checker=authorization_checks,
                    runtime_definition_loader=Mock(return_value=definition),
                    record_loader=Mock(
                        side_effect=AssertionError("dataset must not load")
                    ),
                    credential_loader=Mock(return_value={"api_key": "SECRET"}),
                    readiness_runner=readiness,
                    client_factory_builder=Mock(
                        side_effect=AssertionError("client must not exist")
                    ),
                    phase_runner=Mock(
                        side_effect=AssertionError("generation must not run")
                    ),
                )

            self.assertEqual(authorization_checks.call_count, 2)
            reopened = H0CheckpointStore.open_existing(
                artifacts, "revoked-attempt"
            )
            self.assertEqual(reopened.index["status"], "candidate_failed")
            self.assertEqual(reopened.index["failure_code"], "authorization_revoked")

    async def test_non_rerunnable_terminal_attempt_blocks_before_env_or_readiness(self):
        definition = _definition()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            artifacts = root / "artifacts"
            prior = H0CheckpointStore(
                root=artifacts,
                stage_attempt_id="qualified-attempt",
                candidate_id="Q1",
                phase="H0-A",
            )
            prior.mark_stage_complete("a" * 64)

            with self.assertRaises(H0StateGateError):
                await execute_h0_a_live(
                    root=root,
                    state_path=root / "state.json",
                    artifacts_root=artifacts,
                    stage_attempt_id="forbidden-rerun",
                    authorization_checker=Mock(return_value=_authorization()),
                    runtime_definition_loader=Mock(return_value=definition),
                    record_loader=Mock(
                        side_effect=AssertionError("dataset must not load")
                    ),
                    credential_loader=Mock(
                        side_effect=AssertionError("env must not load")
                    ),
                    readiness_runner=Mock(
                        side_effect=AssertionError("readiness must not run")
                    ),
                    client_factory_builder=Mock(
                        side_effect=AssertionError("client must not exist")
                    ),
                    phase_runner=Mock(
                        side_effect=AssertionError("generation must not run")
                    ),
                )

            self.assertFalse(
                (artifacts / "h0/checkpoints/forbidden-rerun").exists()
            )

    async def test_generation_connectivity_failure_is_durable_and_stops(self):
        definition = _definition()
        client_factory = SimpleNamespace(
            safe_runtime_evidence=lambda: {
                "tokenize_events": [
                    {
                        "endpoint": "/tokenize",
                        "failure_class": "vllm_unreachable",
                        "request_sha256": "5" * 64,
                    }
                ],
                "wire_events": [],
            }
        )

        async def readiness(**kwargs):
            kwargs["credential_loader"]()
            return {
                "schema_version": "membind.h0.readiness-preflight.v1",
                "status": "ready",
                "authorized_candidate_execution_ready": True,
                "generation_requests": 0,
            }

        async def interrupted(**_kwargs):
            raise H0InfrastructureError("vllm_unreachable: private detail")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with self.assertRaisesRegex(H0InfrastructureError, "vllm_unreachable"):
                await execute_h0_a_live(
                    root=root,
                    state_path=root / "state.json",
                    artifacts_root=root / "artifacts",
                    stage_attempt_id="infra-attempt",
                    authorization_checker=Mock(return_value=_authorization()),
                    runtime_definition_loader=Mock(return_value=definition),
                    record_loader=Mock(return_value={"question_id": "07741c45"}),
                    credential_loader=Mock(
                        return_value={
                            "base_url": definition.identity["base_url"],
                            "api_key": "SECRET",
                        }
                    ),
                    readiness_runner=readiness,
                    client_factory_builder=Mock(return_value=client_factory),
                    phase_runner=interrupted,
                )

            reopened = H0CheckpointStore.open_existing(
                root / "artifacts", "infra-attempt"
            )
            self.assertEqual(reopened.index["status"], "infrastructure_interrupted")
            self.assertFalse(reopened.index["candidate_selection_may_continue"])
            self.assertTrue(reopened.index["requires_whole_stage_rerun"])
            failure_entry = reopened.index["segments"][-1]
            self.assertEqual(failure_entry["segment_kind"], "infrastructure_failure")
            persisted = (root / "artifacts" / failure_entry["artifact_path"]).read_text(
                encoding="utf-8"
            )
            self.assertIn("vllm_unreachable", persisted)
            self.assertNotIn("private detail", persisted)
            self.assertNotIn("SECRET", persisted)

    async def test_candidate_failure_is_durable_without_automatic_advance(self):
        definition = _definition()

        async def readiness(**_kwargs):
            return {
                "schema_version": "membind.h0.readiness-preflight.v1",
                "status": "ready",
                "authorized_candidate_execution_ready": True,
                "generation_requests": 0,
            }

        async def candidate_failure(**_kwargs):
            raise H0SemanticError("raw candidate material must not persist")

        factory = SimpleNamespace(
            safe_runtime_evidence=lambda: {"tokenize_events": [], "wire_events": []}
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with self.assertRaises(H0SemanticError):
                await execute_h0_a_live(
                    root=root,
                    state_path=root / "state.json",
                    artifacts_root=root / "artifacts",
                    stage_attempt_id="candidate-attempt",
                    authorization_checker=Mock(return_value=_authorization()),
                    runtime_definition_loader=Mock(return_value=definition),
                    record_loader=Mock(return_value={"question_id": "07741c45"}),
                    credential_loader=Mock(return_value={"api_key": "SECRET"}),
                    readiness_runner=readiness,
                    client_factory_builder=Mock(return_value=factory),
                    phase_runner=candidate_failure,
                )

            reopened = H0CheckpointStore.open_existing(
                root / "artifacts", "candidate-attempt"
            )
            self.assertEqual(reopened.index["status"], "candidate_failed")
            self.assertFalse(reopened.index["candidate_advance_allowed"])
            self.assertEqual(reopened.index["failure_code"], "semantic_utility_failure")
            entry = reopened.index["segments"][-1]
            persisted = (root / "artifacts" / entry["artifact_path"]).read_text(
                encoding="utf-8"
            )
            self.assertNotIn("raw candidate material", persisted)
            self.assertNotIn("SECRET", persisted)


class H0LiveClientFactoryIntegrationTests(IsolatedAsyncioTestCase):
    async def test_real_factory_runs_three_fresh_public_calls_with_zero_retry(self):
        token_requests: list[dict[str, object]] = []
        completion_requests: list[dict[str, object]] = []

        async def tokenize(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/tokenize")
            token_requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                request=request,
                json={"count": 100, "max_model_len": 40960},
            )

        async def complete(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/chat/completions")
            completion_requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "offline",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qwen3-32b-fp8",
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 8,
                        "total_tokens": 108,
                    },
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "extracted_entities": [
                                            {
                                                "name": "Safe",
                                                "entity_type_id": 0,
                                                "episode_indices": [0],
                                            }
                                        ]
                                    }
                                ),
                            },
                        }
                    ],
                },
            )

        candidate = H0CandidateConfig(
            candidate_id="Q1",
            model="qwen3-32b-fp8",
            structured_output_mode="json_schema",
            temperature=0.0,
            top_p=1.0,
            top_k=None,
            min_p=None,
            seed=20260806,
            requested_max_tokens=16384,
            context_limit=40960,
            safety_margin_tokens=32,
        )
        definition = SimpleNamespace(
            identity={
                "base_url": "http://offline.invalid/v1/",
                "served_model_id": "qwen3-32b-fp8",
            },
            candidate=candidate,
            semantic_guardrail=build_h0_semantic_guardrail_manifest(ROOT),
        )
        ledger = H0AttemptLedger(stage_attempt_id="factory-integration")
        collector = H0SemanticEvidenceCollector()
        factory = H0AClientFactory(
            definition=definition,
            credentials={
                "base_url": "http://offline.invalid/v1/",
                "api_key": "OFFLINE_SECRET",
            },
            ledger=ledger,
            semantic_collector=collector,
            completion_transport_factory=lambda: httpx.MockTransport(complete),
            tokenize_transport_factory=lambda: httpx.MockTransport(tokenize),
        )
        checkpoints: list[dict[str, object]] = []

        result = await run_h0_a(
            record=_load_primary_h0_record(ROOT),
            stage_attempt_id="factory-integration",
            client_factory=factory,
            ledger=ledger,
            semantic_collector=collector,
            semantic_guardrail=definition.semantic_guardrail,
            trial_checkpoint=checkpoints.append,
        )

        self.assertTrue(result["qualified"])
        self.assertEqual(factory.created_client_count, 3)
        self.assertEqual(len(token_requests), 3)
        self.assertEqual(len(completion_requests), 3)
        self.assertEqual(len(ledger.trials), 3)
        self.assertEqual(len(ledger.attempts), 3)
        self.assertTrue(all(item["retry_index"] == 0 for item in ledger.attempts))
        self.assertEqual(len(checkpoints), 3)
        safe = json.dumps(factory.safe_runtime_evidence(), sort_keys=True)
        self.assertNotIn("OFFLINE_SECRET", safe)
        self.assertNotIn("Safe", safe)
        self.assertEqual(factory.safe_runtime_evidence()["db_calls"], 0)
        self.assertEqual(factory.safe_runtime_evidence()["embedding_calls"], 0)

if __name__ == "__main__":
    import unittest

    unittest.main()
