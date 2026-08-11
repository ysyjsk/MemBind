"""Contracts for the reference-aligned Qwen transport shim used by C2.

Graphiti owns structured-output prompting, parsing, and retry behavior.  The
project shim may only constrain the single-episode schema, add the frozen Qwen
wire fields, and collect transport telemetry.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from graphiti_core.llm_client.config import LLMConfig  # noqa: E402
from graphiti_core.llm_client.openai_generic_client import (  # noqa: E402
    OpenAIGenericClient,
)
from graphiti_core.prompts.models import Message  # noqa: E402
from graphiti_native import QwenVLLMClient  # noqa: E402
from native_characterization_instrumentation import instrument_llm_client  # noqa: E402
from native_characterization_tracing import TraceRecorder  # noqa: E402
from structured_output import constrain_single_episode_indices  # noqa: E402


class _Edge(BaseModel):
    episode_indices: list[int]


class _Response(BaseModel):
    edges: list[_Edge]


class _Completions:
    def __init__(self, *, content: str = "", error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=17,
                completion_tokens=7,
                total_tokens=24,
            ),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=self.content),
                )
            ],
        )


def _client(completions: _Completions):
    transport = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    return QwenVLLMClient(
        config=LLMConfig(
            api_key="test",
            model="qwen3-32b-fp8",
            small_model="qwen3-32b-fp8",
            base_url="http://127.0.0.1:1/v1",
            temperature=0.0,
            max_tokens=16_384,
        ),
        client=transport,
        max_tokens=16_384,
        structured_output_mode="json_schema",
    )


def _messages() -> list[Message]:
    return [
        Message(role="system", content="system message"),
        Message(role="user", content="user message"),
    ]


class QwenUpstreamTransportShimTests(IsolatedAsyncioTestCase):
    async def test_provider_generation_is_inherited_from_pinned_graphiti(self) -> None:
        client = _client(_Completions(content='{"edges":[]}'))

        self.assertNotIn("_generate_response", type(client).__dict__)
        self.assertNotIn("generate_response", type(client).__dict__)
        self.assertIs(
            type(client)._generate_response,
            OpenAIGenericClient._generate_response,
        )
        self.assertIs(
            type(client).generate_response,
            OpenAIGenericClient.generate_response,
        )

    async def test_only_schema_and_frozen_qwen_wire_fields_differ_from_upstream(self) -> None:
        completions = _Completions(
            content='{"edges":[{"episode_indices":[0]}]}'
        )
        client = _client(completions)
        messages = _messages()
        reference_completions = _Completions(
            content='{"edges":[{"episode_indices":[0]}]}'
        )
        reference = OpenAIGenericClient(
            config=LLMConfig(
                api_key="test",
                model="qwen3-32b-fp8",
                small_model="qwen3-32b-fp8",
                base_url="http://127.0.0.1:1/v1",
                temperature=0.0,
                max_tokens=16_384,
            ),
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=reference_completions)
            ),
            max_tokens=16_384,
            structured_output_mode="json_schema",
        )

        result = await client.generate_response(
            messages,
            response_model=_Response,
            prompt_name="test.reference_aligned",
        )
        reference_result = await reference.generate_response(
            _messages(),
            response_model=_Response,
            prompt_name="test.reference_aligned",
        )

        self.assertEqual(result, {"edges": [{"episode_indices": [0]}]})
        self.assertEqual(result, reference_result)
        self.assertEqual(len(completions.calls), 1)
        request = completions.calls[0]
        reference_request = reference_completions.calls[0]
        self.assertEqual(
            set(request),
            {
                "model",
                "messages",
                "temperature",
                "max_tokens",
                "response_format",
                "top_p",
                "seed",
                "extra_body",
            },
        )
        self.assertEqual(request["max_tokens"], 16_384)
        self.assertEqual(request["top_p"], 1.0)
        self.assertEqual(request["seed"], 20260806)
        self.assertEqual(
            request["extra_body"],
            {"chat_template_kwargs": {"enable_thinking": False}},
        )
        self.assertNotIn("structured_outputs", request)
        for key in ("model", "messages", "temperature", "max_tokens"):
            self.assertEqual(request[key], reference_request[key])

        response_format = request["response_format"]
        self.assertEqual(
            response_format,
            constrain_single_episode_indices(reference_request["response_format"]),
        )
        self.assertEqual(response_format["type"], "json_schema")
        episode_indices = response_format["json_schema"]["schema"]["$defs"][
            "_Edge"
        ]["properties"]["episode_indices"]
        self.assertEqual(episode_indices["minItems"], 1)
        self.assertEqual(episode_indices["maxItems"], 1)
        self.assertEqual(
            episode_indices["items"], {"type": "integer", "const": 0}
        )
        self.assertNotIn(
            "Respond with a JSON object in the following format",
            request["messages"][-1]["content"],
        )

    async def test_upstream_code_fence_stripping_is_retained(self) -> None:
        client = _client(
            _Completions(content='```json\n{"edges":[]}\n```')
        )

        result = await client.generate_response(
            _messages(),
            response_model=_Response,
        )

        self.assertEqual(result, {"edges": []})

    async def test_surrounding_noise_is_not_salvaged(self) -> None:
        client = _client(
            _Completions(content='prefix {"edges":[]} suffix')
        )

        with self.assertRaises(json.JSONDecodeError):
            await client._generate_response(
                _messages(),
                response_model=_Response,
                max_tokens=16_384,
            )

    async def test_public_path_uses_four_upstream_json_retries_and_records_them(self) -> None:
        completions = _Completions(content='prefix {"edges":[]} suffix')
        client = _client(completions)
        recorder = TraceRecorder()
        installed = instrument_llm_client(client, recorder)
        retrying = client._generate_response_with_retry.retry

        try:
            with (
                patch.object(retrying, "sleep", new=AsyncMock()),
                recorder.episode_scope("run", "episode", 0),
                self.assertRaises(json.JSONDecodeError) as raised,
            ):
                await client.generate_response(
                    _messages(),
                    response_model=_Response,
                    prompt_name="test.upstream_json_retry",
                )
        finally:
            installed.restore()

        self.assertEqual(raised.exception.doc, 'prefix {"edges":[]} suffix')
        self.assertEqual(len(completions.calls), 4)
        logical = [record for record in recorder.records if record.phase == "llm"]
        attempts = [
            record for record in recorder.records if record.phase == "llm-transport"
        ]
        self.assertEqual(len(logical), 1)
        self.assertEqual(logical[0].status, "error")
        self.assertEqual(logical[0].metadata["retry_count"], 3)
        self.assertEqual(len(attempts), 4)
        self.assertEqual(
            [record.metadata["attempt_index"] for record in attempts],
            [0, 1, 2, 3],
        )
        self.assertTrue(all(record.status == "ok" for record in attempts))

    async def test_nonretryable_transport_error_is_one_attempt(self) -> None:
        completions = _Completions(error=ValueError("bad response"))
        client = _client(completions)

        with self.assertRaisesRegex(ValueError, "bad response"):
            await client.generate_response(
                _messages(),
                response_model=_Response,
            )

        self.assertEqual(len(completions.calls), 1)

    async def test_c2_instrumentation_observes_and_restores_the_thin_transport(self) -> None:
        completions = _Completions(content='{"edges":[]}')
        client = _client(completions)
        transport = client.client.chat.completions
        recorder = TraceRecorder()

        self.assertNotIn("create", transport.__dict__)
        self.assertNotIn("generate_response", client.__dict__)
        installed = instrument_llm_client(client, recorder)
        try:
            with recorder.episode_scope("run", "episode", 0):
                result = await client.generate_response(
                    _messages(),
                    response_model=_Response,
                    prompt_name="test.instrumented_reference_path",
                )
        finally:
            installed.restore()

        self.assertEqual(result, {"edges": []})
        logical = [record for record in recorder.records if record.phase == "llm"]
        attempts = [
            record for record in recorder.records if record.phase == "llm-transport"
        ]
        self.assertEqual(len(logical), 1)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(logical[0].metadata["retry_count"], 0)
        self.assertEqual(logical[0].metadata["input_tokens"], 17)
        self.assertEqual(logical[0].metadata["output_tokens"], 7)
        self.assertEqual(attempts[0].metadata["attempt_index"], 0)
        self.assertEqual(attempts[0].metadata["input_tokens"], 17)
        self.assertEqual(attempts[0].metadata["output_tokens"], 7)
        self.assertNotIn("create", transport.__dict__)
        self.assertNotIn("generate_response", client.__dict__)

        before = len(recorder.records)
        await client.generate_response(_messages(), response_model=_Response)
        self.assertEqual(len(recorder.records), before)


if __name__ == "__main__":
    import unittest

    unittest.main()
