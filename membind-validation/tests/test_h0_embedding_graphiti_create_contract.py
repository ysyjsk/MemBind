"""RED contracts for Graphiti's single-vector ``EmbedderClient.create`` API.

The installed Graphiti package declares that ``create`` accepts ``list[str]``
and its node/util call sites pass exactly ``[text]`` while expecting one vector.
These tests keep every request inside ``httpx.MockTransport`` and freeze the
minimal adapter behavior needed by that real interface.  No project env,
remote model, database, or SSH endpoint is read or contacted.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import get_args, get_type_hints
from unittest import IsolatedAsyncioTestCase, TestCase

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_embedding  # noqa: E402
from graphiti_core.embedder import EmbedderClient  # noqa: E402
from graphiti_core.llm_client.utils import generate_embedding  # noqa: E402
from graphiti_core.nodes import CommunityNode, EntityNode  # noqa: E402
from h0_embedding import (  # noqa: E402
    EMBEDDING_DIMENSION,
    H0EmbeddingAdapter,
    H0EmbeddingValidationError,
)
from h0_runtime import H0InfrastructureError  # noqa: E402


PRIVATE_TEXT_A = "private graphiti text alpha"
PRIVATE_TEXT_B = "private graphiti text beta"
PRIVATE_API_KEY = "PRIVATE-GRAPHITI-TEST-KEY"


def _unit_vector() -> list[float]:
    return [1.0, *([0.0] * (EMBEDDING_DIMENSION - 1))]


def _embedding_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        json={
            "object": "list",
            "model": "qwen3-embedding-0.6b",
            "data": [
                {
                    "object": "embedding",
                    "index": 0,
                    "embedding": _unit_vector(),
                }
            ],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        },
    )


class _RecordingGraphitiEmbedder(EmbedderClient):
    """Capture the arguments sent by the installed Graphiti call sites."""

    def __init__(self) -> None:
        self.inputs: list[object] = []

    async def create(
        self,
        input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]],
    ) -> list[float]:
        self.inputs.append(input_data)
        return _unit_vector()


class _AdapterFixture:
    binding = {
        "base_url": "http://embedding.invalid/v1",
        "served_model_id": "qwen3-embedding-0.6b",
        "vllm_version": "0.26.0",
        "dimension": EMBEDDING_DIMENSION,
        "normalization": "l2",
    }
    credentials = {
        "base_url": "http://embedding.invalid/v1/",
        "model": "qwen3-embedding-0.6b",
        "api_key": PRIVATE_API_KEY,
    }

    def adapter(self, handler) -> H0EmbeddingAdapter:
        return H0EmbeddingAdapter(
            binding=self.binding,
            credentials=self.credentials,
            transport=httpx.MockTransport(handler),
        )


class InstalledGraphitiCreateContractTests(IsolatedAsyncioTestCase):
    async def test_abstract_signature_and_real_nodes_utils_pass_one_item_text_lists(self):
        hints = get_type_hints(EmbedderClient.create)
        self.assertIn(list[str], get_args(hints["input_data"]))
        self.assertEqual(hints["return"], list[float])

        recorder = _RecordingGraphitiEmbedder()
        entity = EntityNode(name="entity\nname", group_id="offline")
        community = CommunityNode(name="community\nname", group_id="offline")

        entity_vector = await entity.generate_name_embedding(recorder)
        community_vector = await community.generate_name_embedding(recorder)
        util_vector = await generate_embedding(recorder, "utility\ntext")

        self.assertEqual(
            recorder.inputs,
            [["entity name"], ["community name"], ["utility text"]],
        )
        self.assertEqual(entity_vector, _unit_vector())
        self.assertEqual(community_vector, _unit_vector())
        self.assertEqual(util_vector, _unit_vector())

    async def test_adapter_override_keeps_graphiti_list_string_input_contract(self):
        hints = get_type_hints(H0EmbeddingAdapter.create)
        self.assertIn(list[str], get_args(hints["input_data"]))
        self.assertEqual(hints["return"], list[float])


class H0EmbeddingGraphitiIntegrationRedTests(
    _AdapterFixture,
    IsolatedAsyncioTestCase,
):
    async def test_real_entity_and_utils_paths_accept_one_item_list_with_one_request(self):
        async def exercise_entity(adapter: H0EmbeddingAdapter) -> list[float]:
            node = EntityNode(name=PRIVATE_TEXT_A, group_id="offline")
            return await node.generate_name_embedding(adapter)

        async def exercise_util(adapter: H0EmbeddingAdapter) -> list[float]:
            return await generate_embedding(adapter, PRIVATE_TEXT_A)

        for label, exercise in (("entity_node", exercise_entity), ("llm_util", exercise_util)):
            with self.subTest(call_site=label):
                requests: list[httpx.Request] = []

                async def handler(request: httpx.Request) -> httpx.Response:
                    requests.append(request)
                    return _embedding_response(request)

                adapter = self.adapter(handler)
                try:
                    vector = await exercise(adapter)
                    self.assertEqual(vector, _unit_vector())
                    self.assertEqual(len(requests), 1)
                    payload = json.loads(requests[0].content)
                    self.assertEqual(payload["input"], PRIVATE_TEXT_A)

                    evidence = adapter.safe_evidence()
                    self.assertEqual(len(evidence), 1)
                    self.assertEqual(evidence[0]["request_count"], 1)
                    self.assertEqual(evidence[0]["http_attempt_count"], 1)
                    self.assertEqual(evidence[0]["input_count"], 1)
                    persisted = json.dumps(evidence, sort_keys=True)
                    self.assertNotIn(PRIVATE_TEXT_A, persisted)
                    self.assertNotIn(PRIVATE_API_KEY, persisted)
                    self.assertNotIn("Authorization", persisted)
                finally:
                    await adapter.close()

    async def test_exact_one_item_list_is_accepted_but_multi_item_list_fails_closed(self):
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return _embedding_response(request)

        adapter = self.adapter(handler)
        try:
            vector = await adapter.create([PRIVATE_TEXT_A])
            self.assertEqual(vector, _unit_vector())
            self.assertEqual(len(requests), 1)
            success_evidence = adapter.safe_evidence()

            with self.assertRaises(H0EmbeddingValidationError) as raised:
                await adapter.create([PRIVATE_TEXT_A, PRIVATE_TEXT_B])
            self.assertEqual(len(requests), 1)
            self.assertEqual(adapter.safe_evidence(), success_evidence)
            sanitized = str(raised.exception)
            self.assertNotIn(PRIVATE_TEXT_A, sanitized)
            self.assertNotIn(PRIVATE_TEXT_B, sanitized)
            self.assertNotIn(PRIVATE_API_KEY, sanitized)
        finally:
            await adapter.close()

    async def test_one_item_list_preserves_zero_retry_infrastructure_stop_semantics(self):
        scenarios: tuple[str | int, ...] = ("connect", "timeout", 429, 500, 503)
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                calls = 0

                async def handler(
                    request: httpx.Request,
                    selected: str | int = scenario,
                ) -> httpx.Response:
                    nonlocal calls
                    calls += 1
                    if selected == "connect":
                        raise httpx.ConnectError("private connection detail", request=request)
                    if selected == "timeout":
                        raise httpx.ReadTimeout("private timeout detail", request=request)
                    return httpx.Response(
                        selected,
                        request=request,
                        json={"error": {"message": "private server detail"}},
                    )

                adapter = self.adapter(handler)
                try:
                    with self.assertRaisesRegex(
                        H0InfrastructureError,
                        "embedding_unreachable: stop_and_report",
                    ) as raised:
                        await adapter.create([PRIVATE_TEXT_A])
                    self.assertEqual(calls, 1)
                    self.assertEqual(adapter.safe_evidence(), [])
                    self.assertNotIn("private", str(raised.exception))
                    self.assertNotIn(PRIVATE_API_KEY, str(raised.exception))
                finally:
                    await adapter.close()


class H0EmbeddingHarnessInterfaceEvidenceRedTests(TestCase):
    def test_contract_mismatch_projection_is_safe_and_content_independent(self):
        """Classify the historical pre-request mismatch, not model semantics."""

        build = h0_embedding.build_graphiti_create_interface_evidence
        first = build(input_data=[PRIVATE_TEXT_A], http_attempt_count=0)
        second = build(input_data=[PRIVATE_TEXT_B], http_attempt_count=0)

        self.assertEqual(first, second)
        self.assertEqual(
            first,
            {
                "schema_version": "membind.h0.harness-interface-evidence.v1",
                "failure_origin": "execution_harness_interface_contract",
                "failed_boundary": "graphiti_core.embedder.EmbedderClient.create",
                "observed_input_container": "list",
                "observed_input_count": 1,
                "http_attempt_count": 0,
                "candidate_model_failure_supported": False,
                "model_response_content_causally_relevant": False,
                "partial_qualification_reusable": False,
                "old_and_new_trial_counts_mergeable": False,
                "secrets_persisted": False,
                "raw_inputs_persisted": False,
                "raw_responses_persisted": False,
            },
        )
        persisted = json.dumps(first, sort_keys=True)
        self.assertNotIn(PRIVATE_TEXT_A, persisted)
        self.assertNotIn(PRIVATE_TEXT_B, persisted)
        self.assertNotIn(PRIVATE_API_KEY, persisted)

    def test_contract_mismatch_projection_rejects_nonmatching_or_postrequest_events(self):
        build = h0_embedding.build_graphiti_create_interface_evidence
        invalid = (
            {"input_data": PRIVATE_TEXT_A, "http_attempt_count": 0},
            {"input_data": [], "http_attempt_count": 0},
            {"input_data": [PRIVATE_TEXT_A, PRIVATE_TEXT_B], "http_attempt_count": 0},
            {"input_data": [PRIVATE_TEXT_A], "http_attempt_count": 1},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                with self.assertRaises(H0EmbeddingValidationError):
                    build(**arguments)


if __name__ == "__main__":
    import unittest

    unittest.main()
