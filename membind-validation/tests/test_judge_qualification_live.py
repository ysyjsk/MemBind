"""RED contracts for a future, bounded, secret-safe Judge live wrapper.

Every HTTP request in this module terminates in ``httpx.MockTransport``.  The
tests specify the minimum online-qualification boundary without authorizing a
real Judge request, C5 execution, or any dependency on mutable CURRENT_STATE.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import socket
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase, mock

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.schemas import EvaluationItem, EvaluationStatus  # noqa: E402


MODULE_NAME = "evaluation.judge_qualification_live"
PRODUCTION_SOURCE = ROOT / "src/evaluation/judge_qualification_live.py"
PRIVATE_BASE_URL = "http://judge.private.invalid/v1"
PRIVATE_API_KEY = "PRIVATE-JUDGE-CREDENTIAL"
CONFIG_MAPPING = {
    "base_url": PRIVATE_BASE_URL,
    "api_key": PRIVATE_API_KEY,
}
NORMALIZED_BASE_URL = PRIVATE_BASE_URL + "/"
ENDPOINT_SHA256 = hashlib.sha256(NORMALIZED_BASE_URL.encode("utf-8")).hexdigest()


def _production_module():
    """Import lazily so discovery records each missing contract during RED."""

    return importlib.import_module(MODULE_NAME)


def _item(sequence: int) -> EvaluationItem:
    return EvaluationItem(
        item_id=f"qualification-item-{sequence}",
        benchmark="longmemeval",
        question_id=f"question-{sequence}",
        question_type="single-session-user",
        question=f"Where does user {sequence} work?",
        reference_answer="OpenAI",
        hypothesis="The user works at OpenAI.",
    )


def _models_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "list",
            "data": [
                {
                    "id": "qwen3-32b-fp8",
                    "object": "model",
                    "owned_by": "vllm",
                    "root": "qwen3-32b-fp8",
                    "max_model_len": 65536,
                }
            ],
        },
    )


def _completion_response(label: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "mock-qualification",
            "object": "chat.completion",
            "created": 0,
            "model": "qwen3-32b-fp8",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": label},
                    "finish_reason": "stop",
                }
            ],
        },
    )


class JudgeLiveConfigurationRedTests(TestCase):
    def test_explicit_mapping_loads_private_values_but_public_identity_is_hash_only(self) -> None:
        module = _production_module()
        config = module.load_judge_live_config(dict(CONFIG_MAPPING))

        self.assertEqual(config.base_url, NORMALIZED_BASE_URL)
        self.assertEqual(config.api_key, PRIVATE_API_KEY)
        self.assertEqual(
            config.public_identity,
            {
                "endpoint_identity_sha256": ENDPOINT_SHA256,
                "credential_present": True,
                "credential_persisted": False,
            },
        )
        public_rendered = json.dumps(config.public_identity, sort_keys=True)
        self.assertNotIn(PRIVATE_BASE_URL, public_rendered)
        self.assertNotIn("judge.private.invalid", public_rendered)
        self.assertNotIn(PRIVATE_API_KEY, public_rendered)
        self.assertNotIn("api_key", public_rendered.lower())
        self.assertNotIn(PRIVATE_BASE_URL, repr(config))
        self.assertNotIn(PRIVATE_API_KEY, repr(config))

        with self.assertRaises(ValueError):
            module.load_judge_live_config({"base_url": PRIVATE_BASE_URL})
        with self.assertRaises(ValueError):
            module.load_judge_live_config({"api_key": PRIVATE_API_KEY})


class JudgeRuntimeIdentityRedTests(IsolatedAsyncioTestCase):
    async def test_models_identity_is_captured_only_through_mock_transport(self) -> None:
        module = _production_module()
        config = module.load_judge_live_config(dict(CONFIG_MAPPING))
        observed: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            observed.append((request.method, request.url.path))
            self.assertEqual(request.headers.get("authorization"), f"Bearer {PRIVATE_API_KEY}")
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/v1/models")
            return _models_response()

        with mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in Judge RED tests"),
        ):
            identity = await module.capture_judge_runtime_identity(
                config,
                transport=httpx.MockTransport(handler),
            )

        self.assertEqual(observed, [("GET", "/v1/models")])
        self.assertEqual(
            identity,
            {
                "endpoint_identity_sha256": ENDPOINT_SHA256,
                "credential_present": True,
                "credential_persisted": False,
                "served_model_name": "qwen3-32b-fp8",
                "model_root_identity": "qwen3-32b-fp8",
                "max_model_len": 65536,
            },
        )
        rendered = json.dumps(identity, sort_keys=True)
        self.assertNotIn(PRIVATE_BASE_URL, rendered)
        self.assertNotIn("judge.private.invalid", rendered)
        self.assertNotIn(PRIVATE_API_KEY, rendered)
        self.assertNotIn("authorization", rendered.lower())


class BoundedJudgeQualificationRedTests(IsolatedAsyncioTestCase):
    async def test_one_bounded_mock_run_uses_exact_qwen3_wire_config(self) -> None:
        module = _production_module()
        observed: list[dict[str, object]] = []
        labels = iter(("YES", "NO"))

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                self.assertEqual(request.url.path, "/v1/models")
                return _models_response()
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/v1/chat/completions")
            body = json.loads(request.content)
            observed.append(body)
            return _completion_response(next(labels))

        with mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in Judge RED tests"),
        ):
            run = await module.run_bounded_judge_qualification(
                config_mapping=dict(CONFIG_MAPPING),
                items=(_item(0), _item(1)),
                max_items=2,
                transport=httpx.MockTransport(handler),
            )

        self.assertEqual(run.status, "COMPLETE")
        self.assertEqual(run.planned_count, 2)
        self.assertEqual(run.attempted_count, 2)
        self.assertEqual(run.completed_count, 2)
        self.assertEqual(
            [result.status for result in run.results],
            [EvaluationStatus.SUCCESS, EvaluationStatus.SUCCESS],
        )
        self.assertEqual([result.label for result in run.results], [True, False])
        self.assertEqual(run.runtime_identity["served_model_name"], "qwen3-32b-fp8")

        self.assertEqual(len(observed), 2)
        for body in observed:
            self.assertEqual(body["model"], "qwen3-32b-fp8")
            self.assertEqual(body["temperature"], 0)
            self.assertEqual(body["max_tokens"], 10)
            self.assertEqual(body["n"], 1)
            self.assertEqual(
                body["chat_template_kwargs"],
                {"enable_thinking": False},
            )
            messages = body["messages"]
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["role"], "user")
            self.assertNotIn("system", {message["role"] for message in messages})

        rendered = repr(run)
        self.assertNotIn(PRIVATE_BASE_URL, rendered)
        self.assertNotIn(PRIVATE_API_KEY, rendered)

    async def test_service_failure_stops_without_consuming_suffix_and_marks_incomplete(self) -> None:
        module = _production_module()
        chat_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal chat_calls
            if request.method == "GET":
                return _models_response()
            chat_calls += 1
            if chat_calls == 1:
                return _completion_response("YES")
            if chat_calls == 2:
                return httpx.Response(500, json={"error": {"message": "PRIVATE"}})
            raise AssertionError("runner continued after terminal service failure")

        with mock.patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("real network forbidden in Judge RED tests"),
        ):
            run = await module.run_bounded_judge_qualification(
                config_mapping=dict(CONFIG_MAPPING),
                items=(_item(0), _item(1), _item(2)),
                max_items=3,
                transport=httpx.MockTransport(handler),
            )

        self.assertEqual(chat_calls, 2)
        self.assertEqual(run.status, "INCOMPLETE_NON_MERGEABLE")
        self.assertEqual(run.planned_count, 3)
        self.assertEqual(run.attempted_count, 2)
        self.assertEqual(run.completed_count, 1)
        self.assertEqual(len(run.results), 2)
        self.assertEqual(run.results[0].status, EvaluationStatus.SUCCESS)
        self.assertEqual(run.results[1].status, EvaluationStatus.SERVICE_ERROR)
        self.assertEqual(run.failed_item_id, _item(1).item_id)
        self.assertIsNotNone(run.error_class)
        rendered = repr(run)
        self.assertNotIn("PRIVATE", rendered)
        self.assertNotIn(PRIVATE_BASE_URL, rendered)
        self.assertNotIn(PRIVATE_API_KEY, rendered)


class JudgeQualificationIsolationRedTests(TestCase):
    def test_production_module_has_no_current_state_or_c5_dependency(self) -> None:
        self.assertTrue(
            PRODUCTION_SOURCE.is_file(),
            "RED: the minimal Judge qualification wrapper is not implemented",
        )
        source = PRODUCTION_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

        self.assertNotIn("CURRENT_STATE.json", source)
        self.assertFalse(
            any("native_characterization_c5" in name for name in imported_modules),
            imported_modules,
        )
        self.assertFalse(
            any(name.startswith("native_characterization") for name in imported_modules),
            imported_modules,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
