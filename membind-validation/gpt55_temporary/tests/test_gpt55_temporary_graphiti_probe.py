import asyncio
import json
import os
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import gpt55_temporary_graphiti_probe as probe  # noqa: E402
from graphiti_core.llm_client.config import LLMConfig  # noqa: E402
from graphiti_native import QwenVLLMClient  # noqa: E402


class GPT55TemporaryGraphitiProbeTests(IsolatedAsyncioTestCase):
    """Protect the one-off GPT lane from altering frozen vLLM validation state."""

    async def test_preflight_failure_writes_blocked_summary_without_running_graphiti(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            data = Path(tmp) / "data.json"
            data.write_text(json.dumps([{"question_id": "q0"}]), encoding="utf-8")

            async def must_not_run(*args, **kwargs):
                raise AssertionError("run_experiment must not run when GPT preflight fails")

            result = await probe.run_temporary_probe(
                Namespace(
                    data=str(data),
                    question_id="q0",
                    attempt="gpt55_tmp_001",
                    arrival_interval_ms=0,
                    artifacts=str(artifacts),
                    base_url="https://api.labforge.test/v1",
                    api_key="secret",
                    model="gpt-5.5",
                ),
                preflight_fn=lambda **kwargs: {
                    "ok": False,
                    "artifact": "artifacts/diagnostics/preflight.json",
                    "reason": "chat_model_openai_ua did not succeed",
                },
                run_experiment_fn=must_not_run,
                load_instance_fn=lambda path, qid: {"question_id": qid},
            )

            self.assertEqual(result["status"], "blocked_preflight")
            self.assertFalse(result["ok"])
            self.assertEqual(result["lane"], "gpt55_temporary_diagnostic")
            self.assertFalse((artifacts / "CURRENT_STATE.json").exists())
            summary = json.loads(
                (artifacts / "diagnostics" / "gpt55_tmp_001_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["status"], "blocked_preflight")
            self.assertNotIn("secret", repr(summary))

    async def test_successful_preflight_runs_one_m0_live_gpt_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            data = Path(tmp) / "data.json"
            data.write_text(json.dumps([{"question_id": "q0"}]), encoding="utf-8")
            calls = []

            async def fake_run_experiment(spec, instance, arrival_interval_ms, **kwargs):
                calls.append((spec, instance, arrival_interval_ms, kwargs))
                return {
                    "run_id": spec["run_id"],
                    "status": "success",
                    "llm_metrics": {"llm_call_count": 3},
                    "embedding_metrics": {"embedding_call_count": 2},
                }

            old_env = dict(os.environ)
            try:
                os.environ.pop("CONSTRUCTION_LLM_PROVIDER", None)
                os.environ.pop("GPT55_MODEL", None)
                result = await probe.run_temporary_probe(
                    Namespace(
                        data=str(data),
                        question_id="q0",
                        attempt="gpt55_tmp_002",
                        arrival_interval_ms=5,
                        artifacts=str(artifacts),
                        base_url="https://api.labforge.test/v1",
                        api_key="secret",
                        model="gpt-5.5",
                    ),
                    preflight_fn=lambda **kwargs: {
                        "ok": True,
                        "artifact": "artifacts/diagnostics/preflight.json",
                    },
                    run_experiment_fn=fake_run_experiment,
                    load_instance_fn=lambda path, qid: {"question_id": qid},
                )
                self.assertNotIn("CONSTRUCTION_LLM_PROVIDER", os.environ)
                self.assertNotIn("GPT55_MODEL", os.environ)
            finally:
                os.environ.clear()
                os.environ.update(old_env)

            self.assertTrue(result["ok"])
            self.assertEqual(len(calls), 1)
            spec, instance, arrival_interval_ms, kwargs = calls[0]
            self.assertEqual(spec["method"], "M0")
            self.assertEqual(spec["mode"], "live")
            self.assertEqual(spec["lane"], "gpt55_temporary_diagnostic")
            self.assertEqual(spec["run_id"], "gpt55_tmp_002_M0_q0")
            self.assertEqual(arrival_interval_ms, 5)
            self.assertIn("service_checker", kwargs)
            self.assertIn("graphiti_factory", kwargs)
            self.assertNotIn("V3", result["next_allowed_mainline_stage"])


class GPT55TemporaryProbeConfigTests(TestCase):
    """Keep GPT-5.5 adapter behavior in the temporary test lane."""

    def test_default_artifacts_are_inside_the_temporary_lane(self):
        args = probe._parser().parse_args(
            ["--data", "dataset.json", "--question-id", "q0", "--attempt", "tmp"]
        )
        self.assertEqual(args.artifacts, "gpt55_temporary/artifacts")

    def test_preflight_summary_redacts_key_and_requires_chat_success(self):
        report = {
            "tests": [
                {"name": "models_authenticated_openai_ua", "classification": "success"},
                {"name": "chat_model_openai_ua", "classification": "success"},
            ]
        }

        summary = probe.summarize_preflight_report(
            report,
            artifact="artifacts/diagnostics/gpt55_preflight.json",
        )

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["artifact"], "artifacts/diagnostics/gpt55_preflight.json")
        self.assertNotIn("api_key", repr(summary).lower())

    def test_temporary_llm_config_uses_cli_model_and_base_url(self):
        args = Namespace(
            api_key="secret",
            base_url="https://api.labforge.test/v1",
            model="gpt-5.5",
        )

        config = probe._temporary_llm_config(args)

        self.assertIsInstance(config, LLMConfig)
        self.assertEqual(config.api_key, "secret")
        self.assertEqual(config.base_url, "https://api.labforge.test/v1")
        self.assertEqual(config.model, "gpt-5.5")


class GPT55TemporaryAdapterTests(IsolatedAsyncioTestCase):
    """Verify the temporary Chat adapter does not inherit vLLM-only options."""

    async def test_chat_completions_request_omits_vllm_options_and_preserves_messages(self):
        class ResponseModel(BaseModel):
            ok: bool

        response = type(
            "Response",
            (),
            {
                "usage": type(
                    "Usage",
                    (),
                    {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                )(),
                "choices": [
                    type(
                        "Choice",
                        (),
                        {
                            "finish_reason": "stop",
                            "message": type("Message", (), {"content": '{"ok":true}'})(),
                        },
                    )()
                ],
            },
        )()
        client = QwenVLLMClient(
            config=LLMConfig(
                api_key="test",
                model="gpt-5.5",
                small_model="gpt-5.5",
                base_url="http://127.0.0.1:1/v1",
                temperature=0.0,
                max_tokens=2048,
            ),
            max_tokens=2048,
            structured_output_mode="json_schema",
            vllm_options_enabled=False,
        )
        create = AsyncMock(return_value=response)
        client.client = type(
            "Client",
            (),
            {"chat": type("Chat", (), {"completions": type("Completions", (), {"create": create})()})()},
        )()
        messages = [
            type("Msg", (), {"role": "system", "content": "existing graphiti system prompt"})(),
            type("Msg", (), {"role": "user", "content": "return json"})(),
        ]

        parsed = await client._generate_response(
            messages,
            response_model=ResponseModel,
            max_tokens=2048,
        )

        self.assertEqual(parsed, {"ok": True})
        request = create.await_args.kwargs
        self.assertEqual(
            request["messages"],
            [
                {"role": "system", "content": "existing graphiti system prompt"},
                {"role": "user", "content": "return json"},
            ],
        )
        self.assertNotIn("seed", request)
        self.assertNotIn("extra_body", request)
        self.assertEqual(request["model"], "gpt-5.5")
