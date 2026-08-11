"""Offline TDD contracts for the upstream-qualified U0 factory."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from current_state_gate import LiveAction, LiveActionDenied, evaluate_live_action  # noqa: E402
from native_characterization_runtime import (  # noqa: E402
    U0Components,
    U0ConfigurationError,
    build_u0_graphiti_from_env,
)


ENV = {
    "CONSTRUCTION_LLM_API_KEY": "fixture-construction-key",
    "CONSTRUCTION_LLM_BASE_URL": "http://10.87.5.247:8000/v1/",
    "CONSTRUCTION_LLM_MODEL": "qwen3-32b-fp8",
    "EMBEDDING_API_KEY": "fixture-embedding-key",
    "EMBEDDING_BASE_URL": "http://10.87.5.247:8001/v1",
    "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
    "EMBEDDING_DIM": "1024",
    "NEO4J_URI": "bolt://localhost:7687",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "fixture-neo4j-password",
    "GRAPHITI_MAX_COROUTINES": "8",
    "CONSTRUCTION_TOP_P": "1.0",
    "CONSTRUCTION_SEED": "20260806",
    "CONSTRUCTION_OVERFLOW_MAX_TOKENS": "8192",
    "CONSTRUCTION_CONTEXT_SAFETY_TOKENS": "32",
    "CONSTRUCTION_EXPECTED_VLLM_VERSION": "0.26.0",
    "CONSTRUCTION_MIN_CONTEXT_TOKENS": "40960",
}


class _Recorder:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def components(self) -> U0Components:
        self.events.append("components")
        events = self.events

        class LLMConfig:
            def __init__(self, **kwargs):
                events.append("llm-config")
                self.kwargs = kwargs

        class QwenClient:
            def __init__(self, **kwargs):
                events.append("qwen-client")
                self.kwargs = kwargs

        class EmbedderConfig:
            def __init__(self, **kwargs):
                events.append("embedder-config")
                self.kwargs = kwargs

        class Embedder:
            def __init__(self, config):
                events.append("embedder")
                self.config = config

        class Reranker:
            def __init__(self, config):
                events.append("reranker")
                self.config = config

        class Graphiti:
            def __init__(self, **kwargs):
                events.append("graphiti")
                self.kwargs = kwargs

        return U0Components(
            graphiti_type=Graphiti,
            llm_config_type=LLMConfig,
            qwen_client_type=QwenClient,
            embedder_config_type=EmbedderConfig,
            embedder_type=Embedder,
            reranker_type=Reranker,
        )


class NativeCharacterizationU0Tests(TestCase):
    def test_current_offline_state_denies_c0_and_exact_live_scope_is_required(self):
        state = __import__("json").loads((ROOT / "CURRENT_STATE.json").read_text())
        self.assertFalse(
            evaluate_live_action(
                state, LiveAction.NATIVE_CHARACTERIZATION_C0
            ).allowed
        )
        live = dict(state)
        live.update(
            {
                "current_stage": "NATIVE_CHARACTERIZATION",
                "current_action_scope": "native_characterization_c0_live_only",
                "authorized_live_actions": ["native_characterization_c0"],
            }
        )
        self.assertTrue(
            evaluate_live_action(
                live, LiveAction.NATIVE_CHARACTERIZATION_C0
            ).allowed
        )
        live["current_action_scope"] = "native_characterization_offline_only"
        self.assertFalse(
            evaluate_live_action(
                live, LiveAction.NATIVE_CHARACTERIZATION_C0
            ).allowed
        )

    def test_gate_denial_precedes_env_loader_components_and_client_construction(self):
        events: list[str] = []

        def deny(_action):
            events.append("gate")
            raise LiveActionDenied("denied_for_test")

        def env_loader():
            events.append("env")

        recorder = _Recorder(events)
        with self.assertRaisesRegex(LiveActionDenied, "denied_for_test"):
            build_u0_graphiti_from_env(
                authorization_checker=deny,
                env_loader=env_loader,
                component_loader=recorder.components,
            )
        self.assertEqual(events, ["gate"])

    def test_factory_uses_exact_frozen_raw_clients_without_cache_or_stabilizers(self):
        events: list[str] = []

        def allow(action):
            events.append("gate")
            self.assertIs(action, LiveAction.NATIVE_CHARACTERIZATION_C0)

        def env_loader():
            events.append("env")

        recorder = _Recorder(events)
        with patch.dict(os.environ, ENV, clear=True):
            runtime = build_u0_graphiti_from_env(
                authorization_checker=allow,
                env_loader=env_loader,
                component_loader=recorder.components,
            )

        self.assertEqual(events[:3], ["gate", "env", "components"])
        self.assertEqual(runtime.classification, "U0")
        self.assertFalse(runtime.config.prompt_cache)
        self.assertFalse(runtime.config.embedding_cache)
        self.assertFalse(runtime.config.deterministic_candidate_ordering)
        self.assertEqual(runtime.config.requested_max_tokens, 16384)
        self.assertEqual(runtime.config.context_limit, 40960)
        self.assertEqual(runtime.config.safety_margin_tokens, 32)
        graphiti_kwargs = runtime.graphiti.kwargs
        self.assertIs(graphiti_kwargs["llm_client"], runtime.llm_client)
        self.assertIs(graphiti_kwargs["embedder"], runtime.embedder)
        self.assertIs(graphiti_kwargs["cross_encoder"], runtime.reranker)
        self.assertEqual(graphiti_kwargs["max_coroutines"], 8)
        self.assertNotIn("cache", graphiti_kwargs)
        self.assertNotIn("stabilizer", graphiti_kwargs)
        self.assertNotIn("fixture-construction-key", str(runtime.config.to_artifact()))
        self.assertNotIn("fixture-embedding-key", str(runtime.config.to_artifact()))
        self.assertNotIn("fixture-neo4j-password", str(runtime.config.to_artifact()))

    def test_non_sensitive_identity_drift_fails_before_component_import(self):
        events: list[str] = []
        recorder = _Recorder(events)
        drifted = dict(ENV)
        drifted["EMBEDDING_DIM"] = "768"
        with patch.dict(os.environ, drifted, clear=True), self.assertRaisesRegex(
            U0ConfigurationError, "embedding_dimension_mismatch"
        ):
            build_u0_graphiti_from_env(
                authorization_checker=lambda _action: events.append("gate"),
                env_loader=lambda: events.append("env"),
                component_loader=recorder.components,
            )
        self.assertEqual(events, ["gate", "env"])

    def test_request_wire_policy_drift_fails_before_component_import(self):
        cases = {
            "CONSTRUCTION_TOP_P": "0.9",
            "CONSTRUCTION_SEED": "1",
            "CONSTRUCTION_OVERFLOW_MAX_TOKENS": "4096",
            "CONSTRUCTION_CONTEXT_SAFETY_TOKENS": "0",
            "CONSTRUCTION_EXPECTED_VLLM_VERSION": "0.25.0",
            "CONSTRUCTION_MIN_CONTEXT_TOKENS": "32768",
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                events: list[str] = []
                drifted = dict(ENV)
                drifted[name] = value
                with patch.dict(os.environ, drifted, clear=True), self.assertRaisesRegex(
                    U0ConfigurationError, "wire_policy_mismatch"
                ):
                    build_u0_graphiti_from_env(
                        authorization_checker=lambda _action: events.append("gate"),
                        env_loader=lambda: events.append("env"),
                        component_loader=_Recorder(events).components,
                    )
                self.assertEqual(events, ["gate", "env"])

    def test_source_has_no_primary_lane_cache_or_stabilizer_import(self):
        source = (ROOT / "src/native_characterization_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("from deterministic_search", source)
        self.assertNotIn("CachingCountingEmbedder", source)
        self.assertNotIn("GraphitiPromptCacheLLM", source)


if __name__ == "__main__":
    import unittest

    unittest.main()
