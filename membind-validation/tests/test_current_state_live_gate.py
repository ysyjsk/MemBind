"""Fail-closed contracts for every production path that can perform live I/O."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from current_state_gate import (  # noqa: E402
    EXPECTED_PROTOCOL_VERSION,
    LiveAction,
    LiveActionDenied,
    evaluate_live_action,
    require_live_action,
)


def _state(**overrides):
    state = {
        "protocol_version": EXPECTED_PROTOCOL_VERSION,
        "current_stage": "H0",
        "current_action_scope": "h0_offline_tdd_and_harness_only",
        "live_h0_candidate_authorized": False,
        "authorized_live_actions": [],
        "authorized_h0_candidate_id": None,
        "service_admin_authorized": False,
    }
    state.update(overrides)
    return state


def _write_state(path: Path, state) -> None:
    path.write_text(json.dumps(state), encoding="utf-8")


class CurrentStateEvaluatorTests(TestCase):
    def test_missing_current_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(LiveActionDenied, "state_missing"):
                require_live_action(
                    LiveAction.H0_CANDIDATE,
                    state_path=Path(tmp) / "missing.json",
                    candidate_id="Q1",
                )

    def test_invalid_current_state_json_fails_closed_without_leaking_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            secret = "must-not-appear-in-error"
            path.write_text('{"api_key": "' + secret, encoding="utf-8")
            with self.assertRaisesRegex(LiveActionDenied, "state_invalid_json") as raised:
                require_live_action(LiveAction.MODEL_METADATA, state_path=path)
            self.assertNotIn(secret, str(raised.exception))

    def test_wrong_protocol_and_missing_boolean_authorization_fail_closed(self):
        decision = evaluate_live_action(
            _state(protocol_version="current-validation-v1.2"),
            LiveAction.H0_CANDIDATE,
            candidate_id="Q1",
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "protocol_version_mismatch")

        state = _state()
        del state["live_h0_candidate_authorized"]
        decision = evaluate_live_action(
            state, LiveAction.H0_CANDIDATE, candidate_id="Q1"
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "h0_authorization_not_boolean")

    def test_current_offline_state_denies_every_known_live_action(self):
        state = _state()
        for action in LiveAction:
            with self.subTest(action=action.value):
                decision = evaluate_live_action(
                    state,
                    action,
                    candidate_id="Q1" if action is LiveAction.H0_CANDIDATE else None,
                )
                self.assertFalse(decision.allowed)

    def test_native_characterization_c2_requires_exact_stage_scope_and_action(self):
        allowed = _state(
            current_stage="NATIVE_CHARACTERIZATION",
            current_action_scope="native_characterization_c2_live_only",
            authorized_live_actions=["native_characterization_c2"],
        )
        self.assertTrue(
            evaluate_live_action(
                allowed, LiveAction.NATIVE_CHARACTERIZATION_C2
            ).allowed
        )

        variants = [
            {"current_stage": "H0"},
            {"current_action_scope": "native_characterization_c0_live_only"},
            {"authorized_live_actions": ["native_characterization_c0"]},
            {"authorized_live_actions": []},
        ]
        for override in variants:
            with self.subTest(override=override):
                self.assertFalse(
                    evaluate_live_action(
                        {**allowed, **override},
                        LiveAction.NATIVE_CHARACTERIZATION_C2,
                    ).allowed
                )

    def test_repository_state_authorizes_only_native_characterization_c2(self):
        state = json.loads((ROOT / "CURRENT_STATE.json").read_text(encoding="ascii"))
        self.assertEqual(state["current_stage"], "NATIVE_CHARACTERIZATION")
        self.assertEqual(
            state["current_action_scope"], "native_characterization_c2_live_only"
        )
        self.assertEqual(
            state["authorized_live_actions"], ["native_characterization_c2"]
        )
        for action in LiveAction:
            with self.subTest(action=action.value):
                decision = evaluate_live_action(
                    state,
                    action,
                    candidate_id="Q1" if action is LiveAction.H0_CANDIDATE else None,
                )
                self.assertEqual(
                    decision.allowed,
                    action is LiveAction.NATIVE_CHARACTERIZATION_C2,
                )

    def test_h0_candidate_requires_exact_stage_scope_action_and_candidate(self):
        allowed = _state(
            current_action_scope="h0_q1_a_live_only",
            live_h0_candidate_authorized=True,
            authorized_live_actions=["h0_candidate"],
            authorized_h0_candidate_id="Q1",
        )
        self.assertTrue(
            evaluate_live_action(
                allowed, LiveAction.H0_CANDIDATE, candidate_id="Q1"
            ).allowed
        )

        variants = [
            {"current_stage": "V2-R"},
            {"current_action_scope": "h0_offline_tdd_and_harness_only"},
            {"authorized_live_actions": []},
            {"authorized_h0_candidate_id": "Q2"},
        ]
        for override in variants:
            with self.subTest(override=override):
                decision = evaluate_live_action(
                    {**allowed, **override},
                    LiveAction.H0_CANDIDATE,
                    candidate_id="Q1",
                )
                self.assertFalse(decision.allowed)

    def test_h0_authorization_cannot_authorize_other_stages_or_service_admin(self):
        state = _state(
            current_action_scope="h0_q1_a_live_only",
            live_h0_candidate_authorized=True,
            authorized_live_actions=["h0_candidate", "service_admin", "formal"],
            authorized_h0_candidate_id="Q1",
        )
        self.assertFalse(evaluate_live_action(state, LiveAction.FORMAL).allowed)
        self.assertFalse(evaluate_live_action(state, LiveAction.SERVICE_ADMIN).allowed)

    def test_unknown_action_fails_closed(self):
        decision = evaluate_live_action(_state(), "unregistered-action")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "unknown_live_action")


class RuntimeGateOrderingTests(IsolatedAsyncioTestCase):
    async def test_run_experiment_denial_precedes_artifacts_services_and_factory(self):
        from experiment_runner import run_experiment

        service_checker = Mock()
        graphiti_factory = Mock()

        def deny(*_args, **_kwargs):
            raise LiveActionDenied("denied_for_test")

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            with self.assertRaisesRegex(LiveActionDenied, "denied_for_test"):
                await run_experiment(
                    {
                        "run_id": "denied-run",
                        "lane": "formal_performance",
                        "mode": "live",
                        "method": "M0",
                        "question_id": "q0",
                        "repeat": 0,
                    },
                    {"question_id": "q0"},
                    0,
                    artifacts=artifacts,
                    graphiti_factory=graphiti_factory,
                    service_checker=service_checker,
                    authorization_checker=deny,
                )
            self.assertFalse(artifacts.exists())
            service_checker.assert_not_called()
            graphiti_factory.assert_not_called()

    async def test_graphiti_factory_denial_precedes_env_loader(self):
        import graphiti_native

        def deny(*_args, **_kwargs):
            raise LiveActionDenied("denied_before_env")

        with patch.object(graphiti_native, "load_env_file") as env_loader:
            with self.assertRaisesRegex(LiveActionDenied, "denied_before_env"):
                graphiti_native.build_qwen_graphiti_from_env(
                    authorization_checker=deny
                )
        env_loader.assert_not_called()

    async def test_compatibility_probe_denial_precedes_probe_and_output(self):
        import v3_structured_compatibility_probe as compatibility

        def deny(*_args, **_kwargs):
            raise LiveActionDenied("denied_before_compatibility_probe")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "probe.json"
            with patch.object(
                compatibility, "run_compatibility_probe"
            ) as run_probe:
                with self.assertRaisesRegex(
                    LiveActionDenied, "denied_before_compatibility_probe"
                ):
                    await compatibility.write_compatibility_probe(
                        "unused.json",
                        "q0",
                        output,
                        authorization_checker=deny,
                    )
            run_probe.assert_not_called()
            self.assertFalse(output.exists())


class StandaloneProbeAndScriptGateTests(TestCase):
    def test_metadata_probe_denial_precedes_urlopen_and_output(self):
        from vllm_metadata_probe import write_vllm_metadata_probe

        opener = Mock()

        def deny(*_args, **_kwargs):
            raise LiveActionDenied("denied_before_metadata_probe")

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "metadata.json"
            with self.assertRaisesRegex(
                LiveActionDenied, "denied_before_metadata_probe"
            ):
                write_vllm_metadata_probe(
                    "http://model.invalid/v1",
                    None,
                    output,
                    open_url=opener,
                    authorization_checker=deny,
                )
            opener.assert_not_called()
            self.assertFalse(output.exists())

    def test_service_scripts_gate_before_env_socket_download_or_process_start(self):
        expected = {
            "check_local_services.sh": "service_status",
            "start_local_neo4j.sh": "service_admin",
            "start_local_neo4j_daemon.sh": "service_admin",
            "install_local_neo4j.sh": "service_admin",
            "start_embedding_vllm.sh": "service_admin",
        }
        for name, action in expected.items():
            with self.subTest(script=name):
                lines = (ROOT / "scripts" / name).read_text(encoding="utf-8").splitlines()
                gate_index = next(
                    index
                    for index, line in enumerate(lines)
                    if "current_state_gate.py" in line and action in line
                )
                sensitive_tokens = (".env", "socket", "curl ", "neo4j\"", "vllm.entrypoints")
                sensitive_indices = [
                    index
                    for index, line in enumerate(lines)
                    if any(token in line for token in sensitive_tokens)
                ]
                self.assertTrue(sensitive_indices)
                self.assertLess(gate_index, min(sensitive_indices))


if __name__ == "__main__":
    import unittest

    unittest.main()
