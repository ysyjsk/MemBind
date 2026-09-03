import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from graphiti_native import (  # noqa: E402
    QwenVLLMClient,
    clamp_max_tokens,
    constrain_single_episode_indices,
    context_window_from_error,
    llm_metrics,
    safe_structured_request_evidence,
    structured_retry_budgets,
    token_usage_dict,
    wrap_prompt_cache,
)
from graphiti_core.llm_client.config import LLMConfig  # noqa: E402
from response_cache import GraphitiPromptCacheLLM, PromptCache  # noqa: E402
from graphiti_core.llm_client.client import LLMClient  # noqa: E402


class LLMInstrumentationTests(TestCase):
    def test_structured_request_evidence_hashes_but_never_persists_messages(self):
        request = {
            "model": "qwen3-32b-fp8",
            "messages": [
                {"role": "system", "content": "private system"},
                {"role": "user", "content": "private user"},
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 2048,
            "seed": 20260806,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "Result",
                    "schema": {
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                    },
                },
            },
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }

        evidence = safe_structured_request_evidence(request)
        encoded = json.dumps(evidence, sort_keys=True)

        self.assertEqual(evidence["message_count"], 2)
        self.assertEqual(evidence["message_roles"], ["system", "user"])
        self.assertEqual(len(evidence["message_content_sha256"]), 2)
        self.assertEqual(evidence["response_format_type"], "json_schema")
        self.assertEqual(evidence["json_schema_name"], "Result")
        self.assertEqual(evidence["structured_output_backend_requested"], None)
        self.assertEqual(evidence["structured_output_whitespace_mode"], None)
        self.assertFalse(evidence["chat_template_kwargs"]["enable_thinking"])
        self.assertNotIn("private system", encoded)
        self.assertNotIn("private user", encoded)
        self.assertNotIn("messages", evidence)

    def test_graphiti_internal_token_request_is_clamped_to_frozen_protocol_limit(self):
        self.assertEqual(clamp_max_tokens(16_384, 2_048), 2_048)
        self.assertEqual(clamp_max_tokens(512, 2_048), 512)
        self.assertEqual(clamp_max_tokens(None, 2_048), 2_048)

    def test_structured_retry_budget_is_bounded_and_deterministic(self):
        self.assertEqual(structured_retry_budgets(16_384, 2_048, 8_192), (2_048, 8_192))
        self.assertEqual(structured_retry_budgets(512, 2_048, 8_192), (512, 8_192))
        self.assertEqual(structured_retry_budgets(2_048, 2_048, 2_048), (2_048,))

    def test_single_episode_schema_forces_the_only_valid_episode_index(self):
        original = {
            "type": "json_schema",
            "json_schema": {
                "schema": {
                    "$defs": {
                        "Edge": {
                            "properties": {
                                "episode_indices": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                }
                            }
                        }
                    }
                }
            },
        }

        constrained = constrain_single_episode_indices(original)
        field = constrained["json_schema"]["schema"]["$defs"]["Edge"]["properties"]["episode_indices"]
        self.assertEqual(field["minItems"], 1)
        self.assertEqual(field["maxItems"], 1)
        self.assertEqual(field["items"], {"type": "integer", "const": 0})
        self.assertNotIn("maxItems", original["json_schema"]["schema"]["$defs"]["Edge"]["properties"]["episode_indices"])

    def test_context_overflow_error_yields_the_server_context_window(self):
        error = RuntimeError(
            "This model's maximum context length is 32768 tokens. However, you requested "
            "2048 output tokens and your prompt contains at least 30721 input tokens, "
            "for a total of at least 32769 tokens."
        )
        self.assertEqual(context_window_from_error(error), 32768)
        self.assertIsNone(context_window_from_error(RuntimeError("unrelated")))

    def test_token_usage_normalizes_openai_objects_and_none(self):
        usage = SimpleNamespace(prompt_tokens=13, completion_tokens=4, total_tokens=17)

        self.assertEqual(
            token_usage_dict(usage),
            {"prompt_tokens": 13, "completion_tokens": 4, "total_tokens": 17},
        )


class QwenFailureCaptureTests(IsolatedAsyncioTestCase):
    async def test_json_object_retains_upstream_raw_schema_injection(self):
        class Edge(BaseModel):
            episode_indices: list[int]

        class ResponseModel(BaseModel):
            edges: list[Edge]

        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content='{"edges":[{"episode_indices":[0]}]}'
                    ),
                )
            ],
        )
        create = AsyncMock(return_value=response)
        client = QwenVLLMClient(
            config=LLMConfig(
                api_key="test",
                model="qwen3-32b-fp8",
                small_model="qwen3-32b-fp8",
                base_url="http://127.0.0.1:1/v1",
                temperature=0.0,
                max_tokens=2048,
            ),
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
            max_tokens=2048,
            structured_output_mode="json_object",
        )
        messages = [
            SimpleNamespace(role="system", content="system instructions"),
            SimpleNamespace(role="user", content="return json"),
        ]

        parsed = await client.generate_response(
            messages,
            response_model=ResponseModel,
            max_tokens=2048,
        )

        request = create.await_args.kwargs
        marker = "Respond with a JSON object in the following format:"
        upstream_schema_json = json.dumps(ResponseModel.model_json_schema())
        user_content = request["messages"][-1]["content"]

        self.assertEqual(parsed, {"edges": [{"episode_indices": [0]}]})
        self.assertEqual(request["response_format"], {"type": "json_object"})
        self.assertEqual(
            [message["role"] for message in request["messages"]],
            ["system", "user"],
        )
        self.assertEqual(user_content.count(marker), 1)
        self.assertIn(upstream_schema_json, user_content)

    async def test_qwen_vllm_client_keeps_vllm_specific_chat_options(self):
        class ResponseModel(BaseModel):
            ok: bool

        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"ok":true}'),
                )
            ],
        )
        create = AsyncMock(return_value=response)
        client = QwenVLLMClient(
            config=LLMConfig(
                api_key="test",
                model="qwen3-32b-fp8",
                small_model="qwen3-32b-fp8",
                base_url="http://127.0.0.1:1/v1",
                temperature=0.0,
                max_tokens=2048,
            ),
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
            max_tokens=2048,
            structured_output_mode="json_schema",
        )

        await client._generate_response(
            [SimpleNamespace(role="user", content="return json")],
            response_model=ResponseModel,
            max_tokens=2048,
        )

        request = create.await_args.kwargs
        self.assertEqual(request["seed"], 20260806)
        self.assertEqual(
            request["extra_body"],
            {"chat_template_kwargs": {"enable_thinking": False}},
        )

    async def test_recovery_request_binds_server_whitespace_contract_in_evidence(self):
        class ResponseModel(BaseModel):
            ok: bool

        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"ok":true}'),
                )
            ],
        )
        create = AsyncMock(return_value=response)
        certificates = []
        client = QwenVLLMClient(
            config=LLMConfig(
                api_key="test",
                model="qwen3-8b-awq",
                small_model="qwen3-8b-awq",
                base_url="http://127.0.0.1:1/v1",
                temperature=0.0,
                max_tokens=2048,
            ),
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
            max_tokens=2048,
            structured_output_mode="json_schema",
            structured_output_recovery_enabled=True,
            structured_output_token_counter=lambda _messages: 1,
            structured_output_context_limit=65_536,
            structured_output_safety_margin=32,
            structured_output_certificate_sink=certificates.append,
            structured_output_whitespace_mode="disable_any_whitespace_vllm_v1",
            structured_output_whitespace_authority=(
                "authenticated_platform_manifest_process_contract_v1"
            ),
            managed_recovery_enabled=True,
        )

        parsed = await client._generate_response(
            [SimpleNamespace(role="user", content="return json")],
            response_model=ResponseModel,
            max_tokens=2048,
        )

        request = create.await_args.kwargs
        self.assertEqual(parsed, {"ok": True})
        self.assertNotIn("structured_outputs", request["extra_body"])
        self.assertFalse(
            request["extra_body"]["chat_template_kwargs"]["enable_thinking"]
        )
        self.assertEqual(
            client.call_events[-1]["request_evidence"][
                "structured_output_whitespace_mode"
            ],
            "disable_any_whitespace_vllm_v1",
        )
        self.assertEqual(certificates[-1]["whitespace_mode"], "disable_any_whitespace_vllm_v1")
        self.assertTrue(certificates[-1]["physical_serialization_bound"])
        self.assertEqual(
            client.call_events[-1]["request_evidence"][
                "structured_output_whitespace_authority"
            ],
            "authenticated_platform_manifest_process_contract_v1",
        )

    async def test_invalid_structured_response_uses_one_upstream_attempt(self):
        class ResponseModel(BaseModel):
            ok: bool

        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2048, total_tokens=2058),
            choices=[
                SimpleNamespace(
                    finish_reason="length",
                    message=SimpleNamespace(content='{"ok": "unterminated'),
                )
            ],
        )
        create = AsyncMock(return_value=response)
        client = QwenVLLMClient(
            config=LLMConfig(
                api_key="test",
                model="test-model",
                small_model="test-model",
                base_url="http://127.0.0.1:1/v1",
                temperature=0.0,
                max_tokens=2048,
            ),
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
            max_tokens=2048,
            structured_output_mode="json_schema",
        )
        messages = [SimpleNamespace(role="user", content="return json")]

        with self.assertRaises(json.JSONDecodeError):
            await client._generate_response(
                messages,
                response_model=ResponseModel,
                max_tokens=16_384,
            )

        self.assertEqual(len(create.await_args_list), 1)
        self.assertEqual(create.await_args.kwargs["max_tokens"], 16_384)
        self.assertEqual(client.failure_events, [])
        self.assertEqual(client.structured_request_count, 1)
        self.assertEqual(client.structured_response_failure_count, 0)
        record = client.consume_last_call_record()
        self.assertEqual(record["raw_response"], '{"ok": "unterminated')
        self.assertEqual(record["max_tokens"], 16_384)
        self.assertEqual(token_usage_dict(None), {})

    async def test_noisy_structured_response_is_rejected_by_upstream_parser(self):
        class ResponseModel(BaseModel):
            ok: bool

        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=9, total_tokens=20),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content='prefix text {"ok": true} suffix text'
                    ),
                )
            ],
        )
        create = AsyncMock(return_value=response)
        client = QwenVLLMClient(
            config=LLMConfig(
                api_key="test",
                model="test-model",
                small_model="test-model",
                base_url="http://127.0.0.1:1/v1",
                temperature=0.0,
                max_tokens=2048,
            ),
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            ),
            max_tokens=2048,
            structured_output_mode="json_schema",
        )

        with self.assertRaises(json.JSONDecodeError):
            await client._generate_response(
                [SimpleNamespace(role="user", content="return json")],
                response_model=ResponseModel,
                max_tokens=2048,
            )

        self.assertEqual(client.parse_failure_count, 0)
        self.assertEqual(client.structured_response_failure_count, 0)

    async def test_context_error_is_propagated_without_project_probe(self):
        class ResponseModel(BaseModel):
            ok: bool

        error = RuntimeError(
            "maximum context length is 32768 tokens; requested 2048 output tokens; "
            "prompt contains at least 30721 input tokens"
        )
        client = QwenVLLMClient(
            config=LLMConfig(api_key="test", model="m", small_model="m", base_url="http://127.0.0.1:1/v1", temperature=0.0, max_tokens=2048),
            client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=AsyncMock(side_effect=error))
                )
            ),
            max_tokens=2048,
            structured_output_mode="json_schema",
        )
        create = client.client.chat.completions._inner.create

        with self.assertRaisesRegex(RuntimeError, "maximum context length is 32768"):
            await client._generate_response(
                [SimpleNamespace(role="user", content="return json")],
                response_model=ResponseModel,
                max_tokens=16_384,
            )

        self.assertEqual(len(create.await_args_list), 1)
        self.assertEqual(create.await_args.kwargs["max_tokens"], 16_384)
        self.assertEqual(client.structured_request_count, 1)
        self.assertEqual(client.transport_error_count, 1)
        self.assertEqual(client.structured_response_failure_count, 0)

    def test_prompt_cache_wrapper_uses_frozen_revision_and_decoding_config(self):
        inner = SimpleNamespace(config="config", model="model")
        cache = PromptCache(ROOT / "artifacts" / "test-do-not-write.jsonl", read_only=True)

        wrapped = wrap_prompt_cache(inner, cache)

        self.assertIsInstance(wrapped, GraphitiPromptCacheLLM)
        self.assertIsInstance(wrapped, LLMClient)
        self.assertEqual(wrapped.model_revision, "6e2312b85c2ae9a31f629f24493b79d8b02eab1a")
        self.assertEqual(wrapped.decoding_config["seed"], 20260806)
        self.assertEqual(wrapped.decoding_config["temperature"], 0.0)

    def test_llm_metrics_unwraps_cache_and_returns_stable_shape(self):
        inner = SimpleNamespace(
            call_count=7,
            parse_failure_count=1,
            structured_request_count=6,
            structured_response_failure_count=0,
            transport_error_count=2,
            usage_totals={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        )
        wrapper = SimpleNamespace(inner=inner, cache=SimpleNamespace(unexpected_prompt=True))

        self.assertEqual(
            llm_metrics(wrapper),
            {
                "llm_call_count": 7,
                "llm_input_tokens": 100,
                "llm_output_tokens": 20,
                "llm_total_tokens": 120,
                "structured_parse_failures": 1,
                "structured_request_count": 6,
                "structured_response_failures": 0,
                "transport_error_count": 2,
                "unexpected_prompt": True,
            },
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
