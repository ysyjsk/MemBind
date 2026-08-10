"""Offline TDD contracts for the dedicated H0-B/C live runner.

All services and phase primitives are injected fakes.  These tests exercise
gate order, prior-terminal binding, segmented durability, and infrastructure
stop semantics without reading the real project environment or using network.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

H0_B_REPLACEMENT_ATTEMPT_ID = "h0-q1-b-20260809-replacement-001"

from h0_full_history_live import execute_h0_full_history_live  # noqa: E402
from h0_runtime import (  # noqa: E402
    H0CheckpointStore,
    H0InfrastructureError,
    H0ManifestError,
    H0StateGateError,
    canonical_json_sha256,
)


def _authorization(phase: str = "H0-B") -> dict[str, object]:
    return {
        "candidate_id": "Q1",
        "phase": phase,
        "resolved_manifest_index_sha256": "1" * 64,
        "resolved_candidate_manifest_sha256": "2" * 64,
        "resolved_shared_base_manifest_sha256": "3" * 64,
        "prior_phase_completion": {
            "stage_attempt_id": "prior-attempt",
            "checkpoint_index_path": "prior/index.json",
            "checkpoint_index_sha256": "4" * 64,
            "runtime_definition_sha256": "5" * 64,
        },
    }


def _h0_b_harness_repair_admission() -> dict[str, object]:
    return {
        "schema_version": "membind.h0.harness-repair-admission.v1",
        "protocol_version": "current-validation-v1.3",
        "candidate_id": "Q1",
        "phase": "H0-B",
        "decision_path": (
            "artifacts/h0_protocol_repair/decisions/"
            "q1_h0_b_harness_compatibility_repair.json"
        ),
        "decision_sha256": "a" * 64,
        "decision_result_blind": False,
        "prior_model_workload_output_observed": False,
        "repair_required_independent_of_model_output": True,
        "scientific_configuration_unchanged": True,
        "one_shot_whole_stage_replacement": True,
        "replacement_attempt_id": H0_B_REPLACEMENT_ATTEMPT_ID,
        "invalidated_stage_attempt_id": "h0-q1-b-20260809-attempt-001",
        "invalidated_checkpoint_index_sha256": "b" * 64,
        "failure_report_sha256": "c" * 64,
        "old_attempt_qualification_reusable": False,
        "old_and_new_trial_counts_mergeable": False,
        "prior_manifest_index_sha256": "d" * 64,
        "repaired_manifest_index_sha256": "1" * 64,
        "secrets_persisted": False,
    }


def _replacement_authorization() -> dict[str, object]:
    authorization = _authorization()
    authorization["authorized_stage_attempt_id"] = H0_B_REPLACEMENT_ATTEMPT_ID
    authorization["repair_admission"] = _h0_b_harness_repair_admission()
    return authorization


def _definition(phase: str = "H0-B") -> SimpleNamespace:
    return SimpleNamespace(
        identity={
            "candidate_id": "Q1",
            "phase": phase,
            "base_url": "http://construction.invalid/v1/",
            "served_model_id": "qwen3-32b-fp8",
            "vllm_version": "0.26.0",
            "context_limit": 40960,
        },
        candidate=SimpleNamespace(candidate_id="Q1"),
        embedding_namespace={
            "served_model_id": "qwen3-embedding-0.6b",
            "dimension": 1024,
            "normalization": "l2",
        },
        semantic_guardrail={"schema_version": "offline-guardrail"},
        definition_sha256="6" * 64,
    )


def _credentials() -> dict[str, dict[str, str]]:
    return {
        "construction": {
            "base_url": "http://construction.invalid/v1/",
            "api_key": "TEST-CONSTRUCTION-SECRET",
        },
        "embedding": {
            "base_url": "http://10.87.5.247:8001/v1/",
            "model": "qwen3-embedding-0.6b",
            "api_key": "TEST-EMBEDDING-SECRET",
        },
        "neo4j": {
            "uri": "bolt://localhost:7687",
            "user": "neo4j",
            "password": "TEST-NEO4J-SECRET",
            "database": "neo4j",
        },
    }


class _HistoryFactory:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        self.events.append("graph-factory")
        return SimpleNamespace(name=f"graph-{self.calls}")

    def safe_runtime_evidence(self):
        return {
            "fresh_graph_count": self.calls,
            "closed_graph_count": self.calls,
            "embedding_workload_request_count": self.calls,
            "cross_encoder_rank_call_count": 0,
            "histories": [],
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }


class _CheckpointObserved(RuntimeError):
    """Stop a valid admission test immediately after checkpoint construction."""


class H0BReplacementAdmissionTests(IsolatedAsyncioTestCase):
    async def test_exact_replacement_admission_is_forwarded_unchanged_to_checkpoint(self):
        authorization = _replacement_authorization()
        expected_admission = authorization["repair_admission"]
        captured: dict[str, object] = {}

        def checkpoint_store(**kwargs):
            captured.update(kwargs)
            raise _CheckpointObserved("checkpoint boundary reached")

        forbidden = Mock(side_effect=AssertionError("post-checkpoint action is forbidden"))
        with self.assertRaises(_CheckpointObserved):
            await execute_h0_full_history_live(
                root=ROOT,
                state_path=ROOT / "state.json",
                artifacts_root=ROOT / "unused-runs",
                stage_attempt_id=H0_B_REPLACEMENT_ATTEMPT_ID,
                candidate_id="Q1",
                phase="H0-B",
                authorization_checker=Mock(return_value=authorization),
                runtime_definition_loader=Mock(return_value=_definition()),
                prior_completion_validator=Mock(
                    return_value={
                        "qualified": True,
                        "phase": "H0-A",
                        "terminal_result_sha256": "7" * 64,
                    }
                ),
                checkpoint_store_factory=checkpoint_store,
                credential_loader=forbidden,
                readiness_runner=forbidden,
                corpus_loader=forbidden,
                history_factory_builder=forbidden,
                full_history_runner=forbidden,
                phase_runner=forbidden,
                progress_sink=forbidden,
            )

        self.assertEqual(captured["stage_attempt_id"], H0_B_REPLACEMENT_ATTEMPT_ID)
        self.assertEqual(captured["candidate_id"], "Q1")
        self.assertEqual(captured["phase"], "H0-B")
        self.assertEqual(captured["repair_admission"], expected_admission)
        forbidden.assert_not_called()

    async def test_nonreplacement_or_invalid_live_admission_fails_before_runtime(self):
        cases: list[tuple[str, str, dict[str, object]]] = []

        ordinary = _replacement_authorization()
        cases.append(("ordinary_attempt", "ordinary-h0-b-attempt", ordinary))

        wrong_authorized_id = _replacement_authorization()
        wrong_authorized_id["authorized_stage_attempt_id"] = "wrong-replacement"
        cases.append(
            (
                "wrong_authorized_replacement_id",
                H0_B_REPLACEMENT_ATTEMPT_ID,
                wrong_authorized_id,
            )
        )

        missing = _replacement_authorization()
        del missing["repair_admission"]
        cases.append(("missing_admission", H0_B_REPLACEMENT_ATTEMPT_ID, missing))

        mutations = (
            {"schema_version": "membind.h0.harness-repair-admission.v0"},
            {"replacement_attempt_id": "wrong-replacement"},
            {"invalidated_stage_attempt_id": "wrong-failed-attempt"},
            {"decision_result_blind": True},
            {"prior_model_workload_output_observed": True},
            {"repair_required_independent_of_model_output": False},
            {"scientific_configuration_unchanged": False},
            {"one_shot_whole_stage_replacement": False},
            {"old_attempt_qualification_reusable": True},
            {"old_and_new_trial_counts_mergeable": True},
            {"repaired_manifest_index_sha256": "e" * 64},
            {"unexpected_field": False},
        )
        for index, mutation in enumerate(mutations):
            authorization = _replacement_authorization()
            admission = dict(authorization["repair_admission"])
            admission.update(mutation)
            authorization["repair_admission"] = admission
            cases.append(
                (
                    f"tampered_admission_{index}",
                    H0_B_REPLACEMENT_ATTEMPT_ID,
                    authorization,
                )
            )

        for label, attempt_id, authorization in cases:
            with self.subTest(case=label):
                definition = Mock(side_effect=AssertionError("runtime definition touched"))
                checkpoint = Mock(side_effect=AssertionError("checkpoint touched"))
                with self.assertRaises(H0StateGateError):
                    await execute_h0_full_history_live(
                        root=ROOT,
                        state_path=ROOT / "state.json",
                        artifacts_root=ROOT / "unused-runs",
                        stage_attempt_id=attempt_id,
                        candidate_id="Q1",
                        phase="H0-B",
                        authorization_checker=Mock(return_value=authorization),
                        runtime_definition_loader=definition,
                        prior_completion_validator=Mock(
                            side_effect=AssertionError("prior completion touched")
                        ),
                        checkpoint_store_factory=checkpoint,
                        credential_loader=Mock(
                            side_effect=AssertionError("credentials touched")
                        ),
                        readiness_runner=Mock(
                            side_effect=AssertionError("readiness touched")
                        ),
                        corpus_loader=Mock(side_effect=AssertionError("corpus touched")),
                        history_factory_builder=Mock(
                            side_effect=AssertionError("history factory touched")
                        ),
                        full_history_runner=Mock(
                            side_effect=AssertionError("workload touched")
                        ),
                        phase_runner=Mock(side_effect=AssertionError("phase touched")),
                    )
                definition.assert_not_called()
                checkpoint.assert_not_called()


class H0FullHistoryLiveTests(IsolatedAsyncioTestCase):
    async def test_denied_gate_precedes_definition_prior_env_artifacts_and_services(self):
        order: list[str] = []

        def deny(**_kwargs):
            order.append("gate")
            raise H0StateGateError("offline denial")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = root / "runs"
            with self.assertRaises(H0StateGateError):
                await execute_h0_full_history_live(
                    root=root,
                    state_path=root / "state.json",
                    artifacts_root=artifacts,
                    stage_attempt_id="denied-b-attempt",
                    candidate_id="Q1",
                    phase="H0-B",
                    authorization_checker=deny,
                    runtime_definition_loader=Mock(side_effect=AssertionError("definition")),
                    prior_completion_validator=Mock(side_effect=AssertionError("prior")),
                    checkpoint_store_factory=Mock(side_effect=AssertionError("checkpoint")),
                    credential_loader=Mock(side_effect=AssertionError("env")),
                    readiness_runner=Mock(side_effect=AssertionError("readiness")),
                    corpus_loader=Mock(side_effect=AssertionError("dataset")),
                    history_factory_builder=Mock(side_effect=AssertionError("graph")),
                    full_history_runner=Mock(side_effect=AssertionError("history")),
                    phase_runner=Mock(side_effect=AssertionError("phase")),
                )
            self.assertEqual(order, ["gate"])
            self.assertFalse(artifacts.exists())

    async def test_success_validates_prior_then_readiness_and_persists_every_source(self):
        events: list[str] = []
        authorization = _replacement_authorization()
        definition = _definition()
        factory = _HistoryFactory(events)
        gate_calls = 0

        def authorize(**_kwargs):
            nonlocal gate_calls
            gate_calls += 1
            events.append(f"gate-{gate_calls}")
            return dict(authorization)

        def load_definition(_authorization, *, root):
            events.append("definition")
            return definition

        def validate_prior(**kwargs):
            events.append("prior-terminal")
            self.assertEqual(kwargs["authorization"], authorization)
            return {
                "schema_version": "membind.h0.prior-phase-terminal-completion.v1",
                "qualified": True,
                "phase": "H0-A",
                "stage_attempt_id": "prior-attempt",
                "terminal_result_sha256": "7" * 64,
            }

        def load_credentials():
            events.append("env")
            return _credentials()

        async def readiness(**kwargs):
            events.append("readiness")
            kwargs["progress_sink"](
                {
                    "schema_version": "membind.h0.stage-readiness-event.v1",
                    "check": "authorization_recheck",
                    "component": "authorization",
                    "qualified": True,
                    "secrets_persisted": False,
                }
            )
            return {
                "schema_version": "membind.h0.stage-readiness.v1",
                "status": "ready",
                "construction_readiness_count": 1,
                "embedding_readiness_count": 1,
                "neo4j_readiness_count": 1,
                "authorization_recheck_count": 1,
                "generation_requests": 0,
                "embedding_request_count": 0,
                "per_history_warmup_count": 0,
                "secrets_persisted": False,
            }

        def load_corpus(_root):
            events.append("corpus")
            return SimpleNamespace(question_ids=("07741c45",))

        def build_factory(**kwargs):
            events.append("history-factory-built")
            self.assertEqual(kwargs["credentials"], _credentials())
            return factory

        async def full_history(**kwargs):
            events.append("full-history")
            await kwargs["graph_factory"]()
            for sequence in range(2):
                await kwargs["source_checkpoint"](
                    {
                        "schema_version": "membind.h0.phase-checkpoint.v1",
                        "stage_attempt_id": kwargs["stage_attempt_id"],
                        "question_id": "07741c45",
                        "source_sequence": sequence,
                        "logical_call_count": sequence + 1,
                        "http_attempt_count": sequence + 1,
                        "retry_count": 0,
                        "final_stage_checks_passed": sequence == 1,
                        "secrets_persisted": False,
                    }
                )
            return {
                "schema_version": "membind.h0.full-history-evidence.v1",
                "stage_attempt_id": kwargs["stage_attempt_id"],
                "question_id": "07741c45",
                "qualified": True,
                "semantic_records": [
                    {
                        "call_key": "07741c45:0:extract_nodes.extract_message",
                        "response_model_name": "ExtractedEntities",
                        "entity_count": 1,
                        "distinct_normalized_entity_name_count": 1,
                        "semantic_payload_sha256": "8" * 64,
                        "failure_codes": [],
                        "qualified": True,
                    }
                ],
            }

        async def phase_runner(**kwargs):
            events.append("phase")
            item = SimpleNamespace(
                question_id="07741c45",
                instance={"question_id": "07741c45"},
                episodes=(SimpleNamespace(source_sequence=0),),
            )
            history_result = await kwargs["history_runner"](
                item=item,
                stage_attempt_id=kwargs["stage_attempt_id"],
                phase_name=kwargs["phase_name"],
            )
            self.assertTrue(history_result["qualified"])
            return {
                "schema_version": "membind.h0.full-history-phase-result.v1",
                "stage_attempt_id": kwargs["stage_attempt_id"],
                "phase": kwargs["phase_name"],
                "qualified": True,
                "completed_history_count": 1,
                "completed_histories": [
                    {
                        "question_id": "07741c45",
                        "evidence_sha256": canonical_json_sha256(history_result),
                    }
                ],
                "partial_qualification_reusable": True,
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            result = await execute_h0_full_history_live(
                root=root,
                state_path=root / "state.json",
                artifacts_root=root / "runs",
                stage_attempt_id=H0_B_REPLACEMENT_ATTEMPT_ID,
                candidate_id="Q1",
                phase="H0-B",
                authorization_checker=authorize,
                runtime_definition_loader=load_definition,
                prior_completion_validator=validate_prior,
                credential_loader=load_credentials,
                readiness_runner=readiness,
                corpus_loader=load_corpus,
                history_factory_builder=build_factory,
                full_history_runner=full_history,
                phase_runner=phase_runner,
                progress_sink=lambda event: events.append(str(event["status"])),
            )

            self.assertEqual(result["status"], "stage_complete")
            self.assertEqual(result["phase_result"]["qualified"], True)
            self.assertEqual(result["runtime_definition_sha256"], "6" * 64)
            reopened = H0CheckpointStore.open_existing(
                root / "runs", H0_B_REPLACEMENT_ATTEMPT_ID
            )
            self.assertEqual(reopened.index["status"], "stage_complete")
            segment_keys = [
                (entry["segment_kind"], entry["segment_id"])
                for entry in reopened.index["segments"]
            ]
            self.assertEqual(
                segment_keys,
                [
                    ("prior_phase_completion", "qualified"),
                    ("stage_readiness_check", "000-authorization_recheck"),
                    ("stage_readiness_result", "ready"),
                    ("preworkload_progress", "corpus_ready"),
                    ("preworkload_progress", "history_factory_ready"),
                    ("preworkload_progress", "graph_construction_started"),
                    ("preworkload_progress", "graph_construction_ready"),
                    ("source_sequence", "07741c45-000"),
                    ("source_sequence", "07741c45-001"),
                    ("history_result", "07741c45"),
                    ("stage_result", "qualified"),
                ],
            )
            self.assertLess(events.index("prior-terminal"), events.index("env"))
            self.assertLess(events.index("env"), events.index("readiness"))
            self.assertLess(events.index("readiness"), events.index("history-factory-built"))
            self.assertGreaterEqual(gate_calls, 2)

    async def test_graph_construction_failure_persists_last_preworkload_stage(self):
        authorization = _replacement_authorization()

        async def readiness(**kwargs):
            kwargs["progress_sink"](
                {
                    "schema_version": "membind.h0.stage-readiness-event.v1",
                    "check": "authorization_recheck",
                    "component": "authorization",
                    "qualified": True,
                    "secrets_persisted": False,
                }
            )
            return {
                "schema_version": "membind.h0.stage-readiness.v1",
                "status": "ready",
                "construction_readiness_count": 1,
                "embedding_readiness_count": 1,
                "neo4j_readiness_count": 1,
                "authorization_recheck_count": 1,
                "generation_requests": 0,
                "embedding_request_count": 0,
                "per_history_warmup_count": 0,
                "secrets_persisted": False,
            }

        class FailingFactory:
            async def __call__(self):
                raise H0ManifestError("offline graph constructor detail")

            def safe_runtime_evidence(self):
                return {
                    "fresh_graph_count": 0,
                    "closed_graph_count": 0,
                    "embedding_workload_request_count": 0,
                    "cross_encoder_rank_call_count": 0,
                    "histories": [],
                    "secrets_persisted": False,
                    "raw_prompts_persisted": False,
                    "raw_responses_persisted": False,
                }

        async def full_history(**kwargs):
            await kwargs["graph_factory"]()
            raise AssertionError("graph construction failure must stop the workload")

        async def phase_runner(**kwargs):
            item = SimpleNamespace(
                question_id="07741c45",
                instance={"question_id": "07741c45"},
                episodes=(SimpleNamespace(source_sequence=0),),
            )
            await kwargs["history_runner"](
                item=item,
                stage_attempt_id=kwargs["stage_attempt_id"],
                phase_name=kwargs["phase_name"],
            )
            raise AssertionError("failed history must not return to the phase runner")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            with self.assertRaises(H0ManifestError):
                await execute_h0_full_history_live(
                    root=root,
                    state_path=root / "state.json",
                    artifacts_root=root / "runs",
                    stage_attempt_id=H0_B_REPLACEMENT_ATTEMPT_ID,
                    candidate_id="Q1",
                    phase="H0-B",
                    authorization_checker=Mock(return_value=authorization),
                    runtime_definition_loader=Mock(return_value=_definition()),
                    prior_completion_validator=Mock(
                        return_value={
                            "qualified": True,
                            "phase": "H0-A",
                            "terminal_result_sha256": "7" * 64,
                        }
                    ),
                    credential_loader=Mock(return_value=_credentials()),
                    readiness_runner=readiness,
                    corpus_loader=Mock(
                        return_value=SimpleNamespace(question_ids=("07741c45",))
                    ),
                    history_factory_builder=Mock(return_value=FailingFactory()),
                    full_history_runner=full_history,
                    phase_runner=phase_runner,
                    progress_sink=lambda _event: None,
                )

            reopened = H0CheckpointStore.open_existing(
                root / "runs", H0_B_REPLACEMENT_ATTEMPT_ID
            )
            self.assertEqual(reopened.index["status"], "candidate_failed")
            segment_keys = [
                (entry["segment_kind"], entry["segment_id"])
                for entry in reopened.index["segments"]
            ]
            failure_entry = reopened.index["segments"][-1]
            failure_artifact = json.loads(
                (root / "runs" / failure_entry["artifact_path"]).read_text(
                    encoding="ascii"
                )
            )
            failure_payload = failure_artifact["payload"]
            self.assertEqual(
                {
                    "segment_keys": segment_keys,
                    "failure_stage": failure_payload.get("failure_stage"),
                    "failure_code": failure_payload.get("failure_code"),
                },
                {
                    "segment_keys": [
                        ("prior_phase_completion", "qualified"),
                        ("stage_readiness_check", "000-authorization_recheck"),
                        ("stage_readiness_result", "ready"),
                        ("preworkload_progress", "corpus_ready"),
                        ("preworkload_progress", "history_factory_ready"),
                        ("preworkload_progress", "graph_construction_started"),
                        ("candidate_failure", "manifest_contract_failure"),
                    ],
                    "failure_stage": "graph_construction",
                    "failure_code": "manifest_contract_failure",
                },
            )
            self.assertNotIn("offline graph constructor detail", str(failure_payload))

    async def test_embedding_or_neo4j_infrastructure_failure_is_durable_and_stops(self):
        for component in ("embedding", "neo4j"):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                authorization = _replacement_authorization()

                async def readiness(**kwargs):
                    kwargs["progress_sink"](
                        {
                            "schema_version": "membind.h0.stage-readiness-event.v1",
                            "check": f"{component}_readiness_failure",
                            "component": component,
                            "qualified": False,
                            "failure_code": f"{component}_unreachable",
                            "secrets_persisted": False,
                        }
                    )
                    raise H0InfrastructureError(
                        f"{component}_unreachable: stop_and_report"
                    )

                with self.assertRaises(H0InfrastructureError):
                    await execute_h0_full_history_live(
                        root=root,
                        state_path=root / "state.json",
                        artifacts_root=root / "runs",
                        stage_attempt_id=H0_B_REPLACEMENT_ATTEMPT_ID,
                        candidate_id="Q1",
                        phase="H0-B",
                        authorization_checker=Mock(return_value=authorization),
                        runtime_definition_loader=Mock(return_value=_definition()),
                        prior_completion_validator=Mock(
                            return_value={
                                "qualified": True,
                                "phase": "H0-A",
                                "terminal_result_sha256": "7" * 64,
                            }
                        ),
                        credential_loader=Mock(return_value=_credentials()),
                        readiness_runner=readiness,
                        corpus_loader=Mock(side_effect=AssertionError("dataset after readiness")),
                        history_factory_builder=Mock(side_effect=AssertionError("graph")),
                        full_history_runner=AsyncMock(side_effect=AssertionError("history")),
                        phase_runner=AsyncMock(side_effect=AssertionError("phase")),
                        progress_sink=lambda _event: None,
                    )
                reopened = H0CheckpointStore.open_existing(
                    root / "runs", H0_B_REPLACEMENT_ATTEMPT_ID
                )
                self.assertEqual(reopened.index["status"], "infrastructure_interrupted")
                self.assertEqual(reopened.index["stop_reason"], f"{component}_unreachable")
                self.assertFalse(reopened.index["partial_qualification_reusable"])


if __name__ == "__main__":
    import unittest

    unittest.main()
