"""Three RED contracts for the bounded temporary production wiring.

All dependencies are fakes; these tests perform no network, GPU, or database
access.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase

from gpt55_temporary.api_characterization import live_runtime as live


FROZEN_HISTORY_ID = "07741c45"
FROZEN_SOURCE_SEQUENCE = 0
FROZEN_EPISODE_SHA256 = (
    "be983c489b10deea9c4d860f1e3203e4fa5d964154e004b814b2b5fee410156a"
)


@dataclass(frozen=True)
class FakeEpisode:
    question_id: str
    source_sequence: int
    source_hash: str
    body: str = "private synthetic episode body"


class FakeResponseModel:
    @classmethod
    def model_json_schema(cls):
        return {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }

    @classmethod
    def model_validate_json(cls, value: str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("answer"), str):
            raise ValueError("answer is required")
        return SimpleNamespace(model_dump=lambda: dict(parsed))


class FakeChatTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def post_json(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "model": "gpt-5.4-mini",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"answer":"bounded"}',
                    },
                    "finish_reason": "stop",
                }
            ],
        }


class FrozenEpisodeLoaderTests(TestCase):
    def test_selects_07741c45_sequence_zero_and_checks_frozen_hash(self):
        records = [
            {"question_id": "not-selected"},
            {"question_id": FROZEN_HISTORY_ID},
        ]
        loader_calls: list[Path] = []
        builder_calls: list[dict[str, object]] = []

        def records_loader(path: str | Path):
            loader_calls.append(Path(path))
            return records

        def episode_builder(record):
            builder_calls.append(record)
            return [
                FakeEpisode(
                    question_id=FROZEN_HISTORY_ID,
                    source_sequence=FROZEN_SOURCE_SEQUENCE,
                    source_hash=FROZEN_EPISODE_SHA256,
                )
            ]

        episode = live.load_frozen_episode(
            dataset_path=Path("/synthetic/longmemeval_s_cleaned.json"),
            history_id=FROZEN_HISTORY_ID,
            source_sequence=FROZEN_SOURCE_SEQUENCE,
            expected_sha256=FROZEN_EPISODE_SHA256,
            records_loader=records_loader,
            episode_builder=episode_builder,
        )

        self.assertEqual(loader_calls, [Path("/synthetic/longmemeval_s_cleaned.json")])
        self.assertEqual(builder_calls, [records[1]])
        self.assertEqual(episode.question_id, FROZEN_HISTORY_ID)
        self.assertEqual(episode.source_sequence, FROZEN_SOURCE_SEQUENCE)
        self.assertEqual(episode.source_hash, FROZEN_EPISODE_SHA256)


class GraphitiLlmCompatibilityTests(IsolatedAsyncioTestCase):
    async def test_chat_json_schema_has_no_retry_or_forbidden_gpt5_parameters(self):
        transport = FakeChatTransport()
        client = live.BoundedGraphitiLLMClient(
            endpoint="https://relay.example.test/v1/chat/completions",
            api_key="synthetic-secret",
            model="gpt-5.4-mini",
            transport=transport,
            max_api_attempts=8,
            max_tokens=512,
        )
        messages = [
            SimpleNamespace(role="system", content="Graphiti system prompt"),
            SimpleNamespace(role="user", content="Graphiti rendered prompt"),
        ]

        result = await client.generate_response(
            messages,
            response_model=FakeResponseModel,
            max_tokens=192,
            model_size=SimpleNamespace(value="medium"),
            group_id="tmp-api-char-unit",
            prompt_name="extract_nodes.extract_message",
            attribute_extraction=False,
        )

        self.assertEqual(result, {"answer": "bounded"})
        self.assertEqual(len(transport.calls), 1)
        request = transport.calls[0]
        self.assertEqual(request["max_retries"], 0)
        payload = request["payload"]
        self.assertEqual(
            payload["messages"],
            [
                {"role": "system", "content": "Graphiti system prompt"},
                {"role": "user", "content": "Graphiti rendered prompt"},
            ],
        )
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertEqual(
            payload["response_format"]["json_schema"]["schema"],
            FakeResponseModel.model_json_schema(),
        )
        self.assertNotIn("strict", payload["response_format"]["json_schema"])
        for forbidden in ("temperature", "top_p", "seed", "extra_body"):
            self.assertNotIn(forbidden, payload)


class PreflightOrderingTests(IsolatedAsyncioTestCase):
    async def test_http_403_preflight_does_not_create_graphiti(self):
        graphiti_factory_calls: list[object] = []

        async def preflight():
            return {"ok": False, "status_code": 403, "classification": "forbidden"}

        def graphiti_factory():
            graphiti_factory_calls.append(object())
            raise AssertionError("Graphiti must not be constructed after HTTP 403")

        with self.assertRaises(live.PreflightRejected) as raised:
            await live.require_preflight_then_create(
                preflight=preflight,
                graphiti_factory=graphiti_factory,
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(graphiti_factory_calls, [])


class AsyncSdkTransportTests(IsolatedAsyncioTestCase):
    async def test_async_openai_transport_calls_create_once_and_returns_mapping(self):
        calls: list[dict[str, object]] = []

        class Completion:
            def model_dump(self):
                return {
                    "model": "gpt-5.4-mini",
                    "choices": [
                        {
                            "message": {"content": '{"answer":"bounded"}'},
                            "finish_reason": "stop",
                        }
                    ],
                }

        class Create:
            async def create(self, **kwargs):
                calls.append(kwargs)
                return Completion()

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=Create()),
        )
        transport = live.AsyncOpenAIChatTransport(
            endpoint="https://relay.example.test/v1/chat/completions",
            api_key="synthetic-secret",
            timeout_s=10,
            client=client,
        )
        payload = {
            "model": "gpt-5.4-mini",
            "messages": [{"role": "user", "content": "bounded"}],
            "max_tokens": 32,
        }

        response = await transport.post_json(
            url="https://relay.example.test/v1/chat/completions",
            headers={"Authorization": "Bearer synthetic-secret"},
            payload=payload,
            max_retries=0,
        )

        self.assertEqual(calls, [payload])
        self.assertEqual(response["model"], "gpt-5.4-mini")


class TraceAnalysisTests(TestCase):
    def test_remote_api_wall_fraction_uses_interval_union(self):
        spans = [
            {
                "span_id": "root",
                "parent_span_id": None,
                "phase": "add-episode",
                "start_ns": 0,
                "end_ns": 100,
            },
            {
                "span_id": "request-1",
                "parent_span_id": "root",
                "phase": "llm-transport",
                "start_ns": 10,
                "end_ns": 40,
            },
            {
                "span_id": "request-2",
                "parent_span_id": "root",
                "phase": "llm-transport",
                "start_ns": 20,
                "end_ns": 60,
            },
        ]

        analysis = live.analyze_trace_spans(spans)

        self.assertEqual(analysis["add_episode_wall_ns"], 100)
        self.assertEqual(analysis["client_observed_remote_api_wait_union_ns"], 50)
        self.assertEqual(analysis["client_observed_remote_api_request_ns_sum"], 70)
        self.assertEqual(analysis["api_wait_wall_fraction"], 0.5)

    def test_remote_api_wait_excludes_transport_not_descended_from_unique_root(self):
        spans = [
            {
                "span_id": "outside-request",
                "parent_span_id": None,
                "phase": "llm-transport",
                "start_ns": 1,
                "end_ns": 99,
            },
            {
                "span_id": "root",
                "parent_span_id": None,
                "phase": "add-episode",
                "start_ns": 0,
                "end_ns": 100,
            },
            {
                "span_id": "logical-call",
                "parent_span_id": "root",
                "phase": "llm",
                "start_ns": 10,
                "end_ns": 50,
            },
            {
                "span_id": "inside-request",
                "parent_span_id": "logical-call",
                "phase": "llm-transport",
                "start_ns": 20,
                "end_ns": 40,
            },
        ]

        analysis = live.analyze_trace_spans(spans)

        self.assertEqual(analysis["client_observed_remote_api_wait_union_ns"], 20)
        self.assertEqual(analysis["client_observed_remote_api_request_ns_sum"], 20)
        self.assertEqual(analysis["api_wait_wall_fraction"], 0.2)
        self.assertEqual(analysis["phases"]["llm-transport"]["count"], 1)

    def test_descendant_transport_crossing_root_boundary_fails_closed(self):
        spans = [
            {
                "span_id": "root",
                "parent_span_id": None,
                "phase": "add-episode",
                "start_ns": 10,
                "end_ns": 100,
            },
            {
                "span_id": "request",
                "parent_span_id": "root",
                "phase": "llm-transport",
                "start_ns": 0,
                "end_ns": 110,
            },
        ]

        with self.assertRaisesRegex(ValueError, "outside add-episode root"):
            live.analyze_trace_spans(spans)

    def test_unclosed_descendant_transport_fails_closed(self):
        spans = [
            {
                "span_id": "root",
                "parent_span_id": None,
                "phase": "add-episode",
                "start_ns": 0,
                "end_ns": 100,
            },
            {
                "span_id": "request",
                "parent_span_id": "root",
                "phase": "llm-transport",
                "start_ns": 20,
                "end_ns": None,
            },
        ]

        with self.assertRaisesRegex(ValueError, "closed start_ns/end_ns"):
            live.analyze_trace_spans(spans)


class PreflightArtifactTests(TestCase):
    def test_reads_only_complete_matching_success_artifact(self):
        with tempfile.TemporaryDirectory() as raw_tmp:
            attempt = Path(raw_tmp)
            (attempt / "02_transport.json").write_text(
                json.dumps({"http_status": 200, "attempt_count": 1}),
                encoding="utf-8",
            )
            (attempt / "04_summary.json").write_text(
                json.dumps(
                    {
                        "status": "success",
                        "returned_model": "gpt-5.4-mini",
                        "attempt_count": 1,
                    }
                ),
                encoding="utf-8",
            )

            report = live.read_successful_preflight(
                attempt_dir=attempt,
                expected_model="gpt-5.4-mini",
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["status_code"], 200)
