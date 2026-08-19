from __future__ import annotations

import io
import json
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from mab_quality_v2_final_qa.autoresearch import select_probe_qa
from mab_quality_v2_final_qa.live_adapters import normalize_siliconflow_chat_request
from mab_quality_v2_final_qa.live_workflow import (
    _execution_methods,
    _overlay_siliconflow_env,
    _select_execution_contexts,
)
from mab_quality_v2_final_qa.runtime_gate import (
    RuntimeTopology,
    check_embedding_endpoint,
    check_model_endpoint,
)


SILICONFLOW_URL = "https://api.siliconflow.cn/v1"
CHAT_MODEL = "Qwen/Qwen3-32B"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"


class _Response:
    status = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _Opener:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = iter(responses)
        self.requests: list[object] = []

    def open(self, request: object, *, timeout: float):
        self.requests.append(request)
        return next(self.responses)


class SiliconFlowTopologyTests(unittest.TestCase):
    def test_explicit_siliconflow_topology_uses_exact_qwen_models(self) -> None:
        env = {
            "MAB_RUNTIME_PROVIDER": "SILICONFLOW_QWEN",
            "CONSTRUCTION_LLM_BASE_URL": SILICONFLOW_URL,
            "CONSTRUCTION_LLM_MODEL": CHAT_MODEL,
            "QUALITY_LLM_BASE_URL": SILICONFLOW_URL,
            "QUALITY_LLM_MODEL": CHAT_MODEL,
            "EMBEDDING_BASE_URL": SILICONFLOW_URL,
            "EMBEDDING_MODEL": EMBEDDING_MODEL,
            "EMBEDDING_DIM": "1024",
            "NEO4J_URI": "bolt://localhost:7687",
        }
        topology = RuntimeTopology.from_env(env)
        self.assertEqual(topology.provider, "SILICONFLOW_QWEN")
        self.assertEqual(topology.construction.model, CHAT_MODEL)
        self.assertEqual(topology.quality.model, CHAT_MODEL)
        self.assertEqual(topology.embedding.model, EMBEDDING_MODEL)
        self.assertEqual(topology.embedding_dimension, 1024)

    def test_siliconflow_topology_rejects_model_drift(self) -> None:
        env = {
            "MAB_RUNTIME_PROVIDER": "SILICONFLOW_QWEN",
            "CONSTRUCTION_LLM_BASE_URL": SILICONFLOW_URL,
            "CONSTRUCTION_LLM_MODEL": "Qwen/Qwen3-14B",
            "QUALITY_LLM_BASE_URL": SILICONFLOW_URL,
            "QUALITY_LLM_MODEL": CHAT_MODEL,
            "EMBEDDING_BASE_URL": SILICONFLOW_URL,
            "EMBEDDING_MODEL": EMBEDDING_MODEL,
            "EMBEDDING_DIM": "1024",
            "NEO4J_URI": "bolt://localhost:7687",
        }
        with self.assertRaisesRegex(ValueError, "SILICONFLOW_CONSTRUCTION_MODEL_DRIFT"):
            RuntimeTopology.from_env(env)

    def test_process_secret_overlay_does_not_mutate_source_env(self) -> None:
        source = {"NEO4J_URI": "bolt://localhost:7687", "UNCHANGED": "yes"}
        selected = _overlay_siliconflow_env(
            source, {"SILICONFLOW_API_KEY": "test-secret"}
        )
        self.assertNotIn("SILICONFLOW_API_KEY", source)
        self.assertEqual(selected["CONSTRUCTION_LLM_API_KEY"], "test-secret")
        self.assertEqual(selected["QUALITY_LLM_API_KEY"], "test-secret")
        self.assertEqual(selected["EMBEDDING_API_KEY"], "test-secret")
        self.assertEqual(selected["CONSTRUCTION_LLM_MODEL"], CHAT_MODEL)
        self.assertEqual(selected["EMBEDDING_MODEL"], EMBEDDING_MODEL)


class SiliconFlowRequestTests(unittest.TestCase):
    def test_chat_request_maps_thinking_and_removes_vllm_cache_salt(self) -> None:
        request = normalize_siliconflow_chat_request(
            {
                "model": CHAT_MODEL,
                "messages": [{"role": "user", "content": "hello"}],
                "extra_body": {
                    "cache_salt": "not-supported-remotely",
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            }
        )
        self.assertEqual(request["extra_body"], {"enable_thinking": False})
        self.assertNotIn("cache_salt", json.dumps(request))
        self.assertNotIn("chat_template_kwargs", json.dumps(request))

    def test_authenticated_model_probe_accepts_both_exact_ids(self) -> None:
        opener = _Opener(
            [
                _Response(
                    {
                        "data": [
                            {"id": CHAT_MODEL},
                            {"id": EMBEDDING_MODEL},
                        ]
                    }
                )
            ]
        )
        status = check_model_endpoint(
            SILICONFLOW_URL,
            expected_models=(CHAT_MODEL, EMBEDDING_MODEL),
            api_key="test-secret",
            opener=opener,
        )
        self.assertTrue(status.available)
        request = opener.requests[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")

    def test_embedding_probe_requires_exact_model_and_1024_dimensions(self) -> None:
        opener = _Opener(
            [
                _Response(
                    {
                        "model": EMBEDDING_MODEL,
                        "data": [{"embedding": [0.0] * 1024, "index": 0}],
                        "usage": {"prompt_tokens": 2, "total_tokens": 2},
                    }
                )
            ]
        )
        status = check_embedding_endpoint(
            SILICONFLOW_URL,
            model=EMBEDDING_MODEL,
            expected_dimension=1024,
            api_key="test-secret",
            opener=opener,
        )
        self.assertTrue(status.available)
        self.assertEqual(status.dimension, 1024)
        body = json.loads(opener.requests[0].data)
        self.assertEqual(body["model"], EMBEDDING_MODEL)


class StageSelectionTests(unittest.TestCase):
    def test_smoke_runs_only_u0(self) -> None:
        self.assertEqual(_execution_methods("smoke"), ("U0",))
        self.assertEqual(_execution_methods("full"), ("U0", "MEMBIND_V31"))

    def test_smoke_selects_one_context_and_six_deterministic_qa(self) -> None:
        qa_items = tuple(
            SimpleNamespace(
                qa_pair_id=f"qa-{index:02d}",
                question_type=("single-session" if index % 2 else "knowledge-update"),
            )
            for index in range(10)
        )
        context = SimpleNamespace(context_id="context-1", qa_items=qa_items)
        with mock.patch(
            "mab_quality_v2_final_qa.live_workflow.select_probe_contexts",
            return_value=(context,),
        ), mock.patch(
            "mab_quality_v2_final_qa.live_workflow.select_probe_qa",
            return_value=qa_items[:6],
        ):
            selected = _select_execution_contexts((context,), mode="smoke")
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(selected[0].qa_items), 6)


if __name__ == "__main__":
    unittest.main()
