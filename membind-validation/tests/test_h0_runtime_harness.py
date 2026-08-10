"""Offline contracts for the Protocol v1.3 H0 qualification harness.

These tests never load project credentials or contact a model, database, or
remote host.  Live entry must fail closed before any dependency factory runs.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock

import httpx
from pydantic import BaseModel, Field

from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.prompts.models import Message


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_runtime import (  # noqa: E402
    H0AttemptLedger,
    H0BudgetError,
    H0CandidateConfig,
    H0CandidateResult,
    H0DataScopeError,
    H0CheckpointStore,
    H0InfrastructureError,
    H0ManifestError,
    H0QualificationError,
    H0QwenVLLMClient,
    H0SemanticError,
    H0StateGateError,
    H0WireObserver,
    VLLMChatTokenCounter,
    build_h0_completion_request,
    build_h0_openai_client,
    build_h0_workload,
    canonical_json_bytes,
    canonical_json_sha256,
    compute_effective_budget,
    enter_h0_case,
    enter_h0_runtime,
    evaluate_semantic_call,
    load_h0_calibration_corpus,
    load_h0_registry,
    prepare_h0_prompt,
    run_first_passing_candidates,
    sha256_file,
    validate_semantic_stage,
)
from instrumentation import episode_scope  # noqa: E402


class Entity(BaseModel):
    name: str
    episode_indices: list[int] = Field(default_factory=lambda: [0])


class ExtractedEntities(BaseModel):
    extracted_entities: list[Entity]


class H0StateGateTests(TestCase):
    def _write_state(self, directory: str, **updates: object) -> Path:
        state = {
            "protocol_version": "current-validation-v1.3",
            "current_stage": "H0",
            "status": "h0_protocol_accepted_harness_not_implemented",
            "current_action_scope": "h0_offline_tdd_and_harness_only",
            "live_h0_candidate_authorized": False,
            "stage_progress": {"h0_live_gate": "forbidden"},
        }
        state.update(updates)
        path = Path(directory) / "CURRENT_STATE.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        return path

    def test_current_offline_state_rejects_before_env_or_service_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = self._write_state(tmp)
            env_loader = Mock(side_effect=AssertionError("credentials must not be read"))
            service_factory = Mock(side_effect=AssertionError("service must not be built"))

            with self.assertRaisesRegex(H0StateGateError, "live H0 is not authorized"):
                enter_h0_runtime(
                    state_path=state_path,
                    candidate_id="Q1",
                    phase="H0-A",
                    env_loader=env_loader,
                    service_factory=service_factory,
                )

            env_loader.assert_not_called()
            service_factory.assert_not_called()

    def test_only_exact_q1_h0_a_state_can_enter(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = self._write_state(
                tmp,
                status="h0_q1_a_live_only",
                current_action_scope="h0_q1_a_live_only",
                live_h0_candidate_authorized=True,
                authorized_live_actions=["h0_candidate"],
                authorized_h0_candidate_id="Q1",
                stage_progress={"h0_live_gate": "h0_q1_a_live_only"},
                live_h0_authorization={
                    "candidate_id": "Q1",
                    "phase": "H0-A",
                    "resolved_manifest_index_path": "artifacts/h0/index.json",
                    "resolved_manifest_index_sha256": "b" * 64,
                    "resolved_candidate_manifest_path": "artifacts/h0/Q1.json",
                    "resolved_candidate_manifest_sha256": "a" * 64,
                    "resolved_shared_base_manifest_path": "artifacts/h0/shared.json",
                    "resolved_shared_base_manifest_sha256": "c" * 64,
                },
            )
            env_loader = Mock(return_value={"safe": "runtime-config"})
            service = object()
            service_factory = Mock(return_value=service)

            self.assertIs(
                enter_h0_runtime(
                    state_path=state_path,
                    candidate_id="Q1",
                    phase="H0-A",
                    env_loader=env_loader,
                    service_factory=service_factory,
                ),
                service,
            )
            env_loader.assert_called_once_with()
            service_factory.assert_called_once_with({"safe": "runtime-config"})

            for candidate_id, phase in (("Q2", "H0-A"), ("Q1", "H0-B")):
                with self.subTest(candidate_id=candidate_id, phase=phase):
                    with self.assertRaises(H0StateGateError):
                        enter_h0_runtime(
                            state_path=state_path,
                            candidate_id=candidate_id,
                            phase=phase,
                            env_loader=Mock(),
                            service_factory=Mock(),
                        )

    def test_future_candidate_phases_require_matching_exact_machine_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            for candidate_id, phase in (("Q1", "H0-B"), ("Q2", "H0-A"), ("Q3", "H0-C")):
                with self.subTest(candidate_id=candidate_id, phase=phase):
                    suffix = phase.split("-", 1)[1].lower()
                    scope = f"h0_{candidate_id.lower()}_{suffix}_live_only"
                    state_path = self._write_state(
                        tmp,
                        status=scope,
                        current_action_scope=scope,
                        live_h0_candidate_authorized=True,
                        authorized_live_actions=["h0_candidate"],
                        authorized_h0_candidate_id=candidate_id,
                        stage_progress={"h0_live_gate": scope},
                        live_h0_authorization={
                            "candidate_id": candidate_id,
                            "phase": phase,
                            "resolved_manifest_index_path": "artifacts/h0/index.json",
                            "resolved_manifest_index_sha256": "b" * 64,
                            "resolved_candidate_manifest_path": (
                                f"artifacts/h0/{candidate_id}.json"
                            ),
                            "resolved_candidate_manifest_sha256": "a" * 64,
                            "resolved_shared_base_manifest_path": (
                                "artifacts/h0/shared.json"
                            ),
                            "resolved_shared_base_manifest_sha256": "c" * 64,
                        },
                    )
                    service = object()
                    self.assertIs(
                        enter_h0_runtime(
                            state_path=state_path,
                            candidate_id=candidate_id,
                            phase=phase,
                            env_loader=lambda: {},
                            service_factory=lambda _config: service,
                        ),
                        service,
                    )
                    with self.assertRaises(H0StateGateError):
                        enter_h0_runtime(
                            state_path=state_path,
                            candidate_id=candidate_id,
                            phase="H0-A" if phase != "H0-A" else "H0-B",
                            env_loader=Mock(),
                            service_factory=Mock(),
                        )

    def test_live_authorization_requires_all_three_bound_artifact_paths_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            authorization = {
                "candidate_id": "Q1",
                "phase": "H0-A",
                "resolved_manifest_index_path": "artifacts/h0/index.json",
                "resolved_manifest_index_sha256": "1" * 64,
                "resolved_candidate_manifest_path": "artifacts/h0/Q1.json",
                "resolved_candidate_manifest_sha256": "2" * 64,
                "resolved_shared_base_manifest_path": "artifacts/h0/shared.json",
                "resolved_shared_base_manifest_sha256": "3" * 64,
            }
            state_path = self._write_state(
                tmp,
                status="h0_q1_a_live_only",
                current_action_scope="h0_q1_a_live_only",
                live_h0_candidate_authorized=True,
                authorized_live_actions=["h0_candidate"],
                authorized_h0_candidate_id="Q1",
                stage_progress={"h0_live_gate": "h0_q1_a_live_only"},
                live_h0_authorization=authorization,
            )

            for field in (
                "resolved_manifest_index_path",
                "resolved_manifest_index_sha256",
                "resolved_candidate_manifest_path",
                "resolved_candidate_manifest_sha256",
                "resolved_shared_base_manifest_path",
                "resolved_shared_base_manifest_sha256",
            ):
                with self.subTest(field=field):
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    state["live_h0_authorization"].pop(field)
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                    with self.assertRaises(H0StateGateError):
                        enter_h0_runtime(
                            state_path=state_path,
                            candidate_id="Q1",
                            phase="H0-A",
                            env_loader=Mock(),
                            service_factory=Mock(),
                        )
                    state["live_h0_authorization"] = dict(authorization)
                    state_path.write_text(json.dumps(state), encoding="utf-8")


class H0BudgetAndSchemaTests(TestCase):
    def _candidate(self, candidate_id: str) -> H0CandidateConfig:
        values = {
            "Q1": ("json_schema", 0.0, 1.0, None, None),
            "Q2": ("json_schema", 0.7, 0.8, 20, 0),
            "Q3": ("json_object", 0.7, 0.8, 20, 0),
        }
        mode, temperature, top_p, top_k, min_p = values[candidate_id]
        return H0CandidateConfig(
            candidate_id=candidate_id,
            model="qwen3-32b-fp8",
            structured_output_mode=mode,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            min_p=min_p,
            seed=20260806,
            requested_max_tokens=16384,
            context_limit=40960,
            safety_margin_tokens=32,
        )

    def test_effective_budget_is_computed_before_send_without_prompt_truncation(self):
        messages = (
            {"role": "system", "content": "system bytes stay fixed"},
            {"role": "user", "content": "user bytes stay fixed"},
        )
        original = json.dumps(messages, sort_keys=True)

        budget = compute_effective_budget(
            requested_max_tokens=16384,
            context_limit=40960,
            prompt_tokens=32757,
            safety_margin_tokens=32,
        )

        self.assertEqual(budget.effective_max_tokens, 8171)
        self.assertEqual(budget.requested_max_tokens, 16384)
        self.assertEqual(json.dumps(messages, sort_keys=True), original)

    def test_nonpositive_context_remainder_fails_before_request_plan_exists(self):
        with self.assertRaisesRegex(H0BudgetError, "context_budget_insufficient") as caught:
            compute_effective_budget(
                requested_max_tokens=16384,
                context_limit=40960,
                prompt_tokens=40928,
                safety_margin_tokens=32,
            )
        self.assertEqual(caught.exception.evidence["effective_max_tokens"], 0)

    def test_q1_q2_use_effective_schema_and_q3_injects_that_exact_schema(self):
        messages = [SimpleNamespace(role="user", content="return structured data")]

        q1_prompt = prepare_h0_prompt(messages, ExtractedEntities, "json_schema")
        q3_prompt = prepare_h0_prompt(messages, ExtractedEntities, "json_object")

        self.assertNotEqual(
            q1_prompt.schema.upstream_schema_sha256,
            q1_prompt.schema.effective_schema_sha256,
        )
        constrained = q1_prompt.schema.effective_schema["$defs"]["Entity"]["properties"]
        self.assertEqual(
            constrained["episode_indices"],
            {
                "items": {"const": 0, "type": "integer"},
                "maxItems": 1,
                "minItems": 1,
                "title": "Episode Indices",
                "type": "array",
            },
        )
        self.assertIsNone(q1_prompt.injected_schema_sha256)
        self.assertEqual(
            q3_prompt.injected_schema_sha256,
            q3_prompt.schema.effective_schema_sha256,
        )
        self.assertIn(q3_prompt.schema.effective_schema_json, q3_prompt.messages[-1]["content"])
        self.assertNotIn(q3_prompt.schema.upstream_schema_json, q3_prompt.messages[-1]["content"])
        self.assertEqual(messages[0].content, "return structured data")

    def test_candidate_request_records_all_budget_and_payload_fields(self):
        prepared = prepare_h0_prompt(
            [SimpleNamespace(role="user", content="payload")],
            ExtractedEntities,
            "json_schema",
        )

        q1 = build_h0_completion_request(self._candidate("Q1"), prepared, prompt_tokens=100)
        q2 = build_h0_completion_request(self._candidate("Q2"), prepared, prompt_tokens=100)

        self.assertNotIn("top_k", q1.payload)
        self.assertNotIn("min_p", q1.payload)
        self.assertNotIn("top_k", q1.payload["extra_body"])
        self.assertNotIn("min_p", q1.payload["extra_body"])
        self.assertEqual(q2.payload["extra_body"]["top_k"], 20)
        self.assertEqual(q2.payload["extra_body"]["min_p"], 0)
        self.assertEqual(q2.evidence["top_k"], 20)
        self.assertEqual(q2.evidence["min_p"], 0)
        for field in (
            "requested_max_tokens",
            "effective_max_tokens",
            "context_limit",
            "prompt_tokens",
            "safety_margin_tokens",
            "requested_request_payload_sha256",
        ):
            self.assertIn(field, q2.evidence)
        self.assertNotIn("messages", q2.evidence)


class H0WireSerializationTests(IsolatedAsyncioTestCase):
    async def test_sdk_wire_body_observer_proves_q2_fields_and_disables_hidden_retry(self):
        wire_bodies: list[dict[str, object]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            wire_bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "offline",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qwen3-32b-fp8",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": '{"extracted_entities":[]}',
                            },
                        }
                    ],
                },
            )

        observer = H0WireObserver()
        client = build_h0_openai_client(
            api_key="offline-test-key",
            base_url="http://offline.invalid/v1",
            observer=observer,
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(client._platform, "Linux")
        candidate = H0BudgetAndSchemaTests()._candidate("Q2")
        prepared = prepare_h0_prompt(
            [SimpleNamespace(role="user", content="never persist this raw prompt")],
            ExtractedEntities,
            "json_schema",
        )
        plan = build_h0_completion_request(candidate, prepared, prompt_tokens=100)

        try:
            await client.chat.completions.create(**plan.payload)
        finally:
            await client.close()

        self.assertEqual(client.max_retries, 0)
        self.assertEqual(len(wire_bodies), 1)
        self.assertEqual(wire_bodies[0]["top_k"], 20)
        self.assertEqual(wire_bodies[0]["min_p"], 0)
        self.assertFalse(wire_bodies[0]["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(len(observer.events), 1)
        event_text = json.dumps(observer.events, sort_keys=True)
        self.assertNotIn("never persist this raw prompt", event_text)
        self.assertNotIn("offline-test-key", event_text)
        self.assertNotIn("Authorization", event_text)
        self.assertEqual(observer.events[0]["top_k"], 20)
        self.assertEqual(observer.events[0]["min_p"], 0)
        self.assertRegex(
            observer.events[0]["observed_request_payload_sha256"], r"^[0-9a-f]{64}$"
        )


class H0ClientIntegrationTests(IsolatedAsyncioTestCase):
    def _candidate(self, candidate_id: str) -> H0CandidateConfig:
        return H0BudgetAndSchemaTests()._candidate(candidate_id)

    def _call_key_client(self):
        async def deny_wire_access(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"call-key test attempted wire access: {request.url}")

        async def unused_token_counter(
            _model: str, _messages: list[dict[str, str]]
        ) -> int:
            raise AssertionError("call-key test attempted tokenization")

        openai_client = build_h0_openai_client(
            api_key="offline-test-key",
            base_url="http://offline.invalid/v1",
            observer=H0WireObserver(),
            transport=httpx.MockTransport(deny_wire_access),
        )
        client = H0QwenVLLMClient(
            config=LLMConfig(
                api_key="unused-by-injected-client",
                model="qwen3-32b-fp8",
                small_model="qwen3-32b-fp8",
                base_url="http://offline.invalid/v1",
                temperature=0.0,
                max_tokens=16384,
            ),
            candidate=self._candidate("Q1"),
            token_counter=unused_token_counter,
            semantic_guardrail={},
            ledger=H0AttemptLedger(stage_attempt_id="offline-call-key"),
            client=openai_client,
        )
        return client, openai_client

    async def test_graphiti_implicit_group_uses_episode_question_id(self):
        client, openai_client = self._call_key_client()
        try:
            with episode_scope("07741c45", 1):
                call_key = client._h0_call_key(None, "dedupe_nodes.nodes")
        finally:
            await openai_client.close()

        self.assertEqual(call_key, "07741c45:1:dedupe_nodes.nodes")

    async def test_h0_a_explicit_group_id_remains_authoritative(self):
        client, openai_client = self._call_key_client()
        try:
            with episode_scope("h0-q1-a-attempt", 0):
                call_key = client._h0_call_key(
                    "07741c45", "extract_nodes.extract_message"
                )
        finally:
            await openai_client.close()

        self.assertEqual(call_key, "07741c45:0:extract_nodes.extract_message")

    async def test_public_q3_call_uses_exact_counter_one_wire_attempt_and_safe_ledger(self):
        wire_bodies: list[dict[str, object]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            wire_bodies.append(body)
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "offline",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qwen3-32b-fp8",
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 8,
                        "total_tokens": 108,
                    },
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "extracted_entities": [
                                            {"name": "Safe", "episode_indices": [0]}
                                        ]
                                    }
                                ),
                            },
                        }
                    ],
                },
            )

        counted_messages: list[list[dict[str, str]]] = []

        async def count_tokens(model: str, messages: list[dict[str, str]]) -> int:
            self.assertEqual(model, "qwen3-32b-fp8")
            counted_messages.append(messages)
            return 100

        observer = H0WireObserver()
        openai_client = build_h0_openai_client(
            api_key="offline-test-key",
            base_url="http://offline.invalid/v1",
            observer=observer,
            transport=httpx.MockTransport(handler),
        )
        ledger = H0AttemptLedger(stage_attempt_id="offline-client-001")
        guardrail = H0SemanticUtilityTests()._guardrail()
        semantic_events: list[dict[str, object]] = []
        client = H0QwenVLLMClient(
            config=LLMConfig(
                api_key="unused-by-injected-client",
                model="qwen3-32b-fp8",
                small_model="qwen3-32b-fp8",
                base_url="http://offline.invalid/v1",
                temperature=0.7,
                max_tokens=16384,
            ),
            candidate=self._candidate("Q3"),
            token_counter=count_tokens,
            semantic_guardrail=guardrail,
            semantic_evidence_sink=semantic_events.append,
            ledger=ledger,
            client=openai_client,
        )
        try:
            with episode_scope("offline-run", 0):
                parsed = await client.generate_response(
                    [
                        Message(role="system", content="SYSTEM_RAW_SENTINEL"),
                        Message(role="user", content="USER_RAW_SENTINEL"),
                    ],
                    response_model=ExtractedEntities,
                    group_id="07741c45",
                    prompt_name="extract_nodes.extract_message",
                )
        finally:
            await openai_client.close()

        self.assertEqual(parsed["extracted_entities"][0]["name"], "Safe")
        self.assertEqual(len(counted_messages), 1)
        self.assertEqual(len(wire_bodies), 1)
        self.assertEqual(wire_bodies[0]["response_format"], {"type": "json_object"})
        self.assertIn('"const":0', wire_bodies[0]["messages"][-1]["content"])
        self.assertEqual(len(ledger.trials), 1)
        self.assertEqual(len(ledger.attempts), 1)
        self.assertEqual(len(semantic_events), 1)
        self.assertEqual(
            set(semantic_events[0]),
            {
                "call_key",
                "response_model_name",
                "entity_count",
                "distinct_normalized_entity_name_count",
                "semantic_payload_sha256",
                "failure_codes",
                "qualified",
                "repeated_trial_index",
            },
        )
        self.assertNotIn("Safe", json.dumps(semantic_events, sort_keys=True))
        logical_id = next(iter(ledger.trials))
        self.assertTrue(ledger.trial_verdict(logical_id)["qualified"])
        persisted = json.dumps(ledger.safe_artifact(), sort_keys=True)
        self.assertNotIn("Safe", persisted)
        self.assertNotIn("SYSTEM_RAW_SENTINEL", persisted)
        self.assertNotIn("USER_RAW_SENTINEL", persisted)

    async def test_concurrent_calls_attach_their_own_observed_wire_event(self):
        release_first = asyncio.Event()
        first_seen = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            user_text = body["messages"][-1]["content"]
            if "FIRST_CONCURRENT_RAW" in user_text:
                first_seen.set()
                await release_first.wait()
            else:
                await first_seen.wait()
                release_first.set()
            return httpx.Response(
                200,
                request=request,
                json={
                    "id": "offline",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qwen3-32b-fp8",
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 8,
                        "total_tokens": 108,
                    },
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "extracted_entities": [
                                            {"name": "Safe", "episode_indices": [0]}
                                        ]
                                    }
                                ),
                            },
                        }
                    ],
                },
            )

        async def count_tokens(_model: str, _messages: list[dict[str, str]]) -> int:
            return 100

        observer = H0WireObserver()
        openai_client = build_h0_openai_client(
            api_key="offline-test-key",
            base_url="http://offline.invalid/v1",
            observer=observer,
            transport=httpx.MockTransport(handler),
        )
        ledger = H0AttemptLedger(stage_attempt_id="offline-concurrent-wire")
        client = H0QwenVLLMClient(
            config=LLMConfig(
                api_key="unused-by-injected-client",
                model="qwen3-32b-fp8",
                small_model="qwen3-32b-fp8",
                base_url="http://offline.invalid/v1",
                temperature=0.7,
                max_tokens=16384,
            ),
            candidate=self._candidate("Q1"),
            token_counter=count_tokens,
            semantic_guardrail=H0SemanticUtilityTests()._guardrail(),
            ledger=ledger,
            client=openai_client,
        )

        async def one_call(text: str, prompt_name: str) -> dict[str, object]:
            with episode_scope("offline-run", 0):
                return await client.generate_response(
                    [
                        Message(role="system", content="SYSTEM_RAW_SENTINEL"),
                        Message(role="user", content=text),
                    ],
                    response_model=ExtractedEntities,
                    group_id="offline-run",
                    prompt_name=prompt_name,
                )

        try:
            first, second = await asyncio.gather(
                one_call("FIRST_CONCURRENT_RAW", "extract_nodes.first"),
                one_call("SECOND_CONCURRENT_RAW", "extract_nodes.second"),
            )
        finally:
            await openai_client.close()

        self.assertIn("extracted_entities", first)
        self.assertIn("extracted_entities", second)
        artifact = ledger.safe_artifact()
        attempts = artifact["http_attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(item["completed"] for item in attempts))
        self.assertTrue(all(item["http_200"] for item in attempts))
        self.assertTrue(all(item["json_parse_success"] for item in attempts))
        self.assertTrue(all(item["pydantic_validation_success"] for item in attempts))
        self.assertTrue(all(item["semantic_utility_success"] for item in attempts))
        self.assertTrue(
            all("observed_request_payload_sha256" in item for item in attempts)
        )
        self.assertEqual(
            {tuple(item["message_content_sha256"]) for item in attempts},
            {tuple(item["observed_message_content_sha256"]) for item in attempts},
        )
        self.assertRegex(
            attempts[0]["observed_request_payload_sha256"], r"^[0-9a-f]{64}$"
        )
        self.assertRegex(
            attempts[1]["observed_request_payload_sha256"], r"^[0-9a-f]{64}$"
        )
        self.assertNotIn("FIRST_CONCURRENT_RAW", json.dumps(artifact))
        self.assertNotIn("SECOND_CONCURRENT_RAW", json.dumps(artifact))

    async def test_cancelled_concurrent_call_finishes_attempt_record(self):
        slow_started = asyncio.Event()
        release_slow = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            user_text = body["messages"][-1]["content"]
            if "SLOW_CANCEL_RAW" in user_text:
                slow_started.set()
                await release_slow.wait()
            await slow_started.wait()
            raise httpx.ConnectError("offline sibling failure", request=request)

        async def count_tokens(_model: str, _messages: list[dict[str, str]]) -> int:
            return 100

        observer = H0WireObserver()
        openai_client = build_h0_openai_client(
            api_key="offline-test-key",
            base_url="http://offline.invalid/v1",
            observer=observer,
            transport=httpx.MockTransport(handler),
        )
        ledger = H0AttemptLedger(stage_attempt_id="offline-cancelled-sibling")
        client = H0QwenVLLMClient(
            config=LLMConfig(
                api_key="unused-by-injected-client",
                model="qwen3-32b-fp8",
                small_model="qwen3-32b-fp8",
                base_url="http://offline.invalid/v1",
                temperature=0.7,
                max_tokens=16384,
            ),
            candidate=self._candidate("Q1"),
            token_counter=count_tokens,
            semantic_guardrail=H0SemanticUtilityTests()._guardrail(),
            ledger=ledger,
            client=openai_client,
        )

        async def one_call(text: str, prompt_name: str) -> dict[str, object]:
            with episode_scope("offline-run", 0):
                return await client.generate_response(
                    [
                        Message(role="system", content="SYSTEM_RAW_SENTINEL"),
                        Message(role="user", content=text),
                    ],
                    response_model=ExtractedEntities,
                    group_id="offline-run",
                    prompt_name=prompt_name,
                )

        slow_task = asyncio.create_task(
            one_call("SLOW_CANCEL_RAW", "dedupe_edges.resolve_edge.slow")
        )
        try:
            await slow_started.wait()
            with self.assertRaisesRegex(H0InfrastructureError, "vllm_unreachable"):
                await one_call("FAIL_INFRA_RAW", "dedupe_edges.resolve_edge.fail")
            slow_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await slow_task
        finally:
            release_slow.set()
            await openai_client.close()

        attempts = ledger.safe_artifact()["http_attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(item["completed"] for item in attempts))
        self.assertEqual(
            {item["failure_class"] for item in attempts},
            {"vllm_unreachable", "concurrent_attempt_cancelled"},
        )

    async def test_vllm_connection_failure_stops_after_one_wire_attempt(self):
        wire_count = 0

        async def unavailable(request: httpx.Request) -> httpx.Response:
            nonlocal wire_count
            wire_count += 1
            raise httpx.ConnectError("offline simulated refusal", request=request)

        async def count_tokens(_model: str, _messages: list[dict[str, str]]) -> int:
            return 100

        observer = H0WireObserver()
        openai_client = build_h0_openai_client(
            api_key="offline-test-key",
            base_url="http://offline.invalid/v1",
            observer=observer,
            transport=httpx.MockTransport(unavailable),
        )
        ledger = H0AttemptLedger(stage_attempt_id="offline-client-002")
        client = H0QwenVLLMClient(
            config=LLMConfig(
                api_key="unused",
                model="qwen3-32b-fp8",
                small_model="qwen3-32b-fp8",
                base_url="http://offline.invalid/v1",
                temperature=0.0,
                max_tokens=16384,
            ),
            candidate=self._candidate("Q1"),
            token_counter=count_tokens,
            semantic_guardrail=H0SemanticUtilityTests()._guardrail(),
            ledger=ledger,
            client=openai_client,
        )
        try:
            with episode_scope("offline-run", 0):
                with self.assertRaisesRegex(H0InfrastructureError, "vllm_unreachable"):
                    await client.generate_response(
                        [Message(role="system", content="s"), Message(role="user", content="u")],
                        response_model=ExtractedEntities,
                        group_id="07741c45",
                        prompt_name="extract_nodes.extract_message",
                    )
        finally:
            await openai_client.close()

        self.assertEqual(wire_count, 1)
        self.assertEqual(len(ledger.attempts), 1)
        self.assertEqual(ledger.attempts[0]["failure_class"], "vllm_unreachable")
        persisted = json.dumps(ledger.safe_artifact(), sort_keys=True)
        self.assertNotIn("offline simulated refusal", persisted)

    async def test_completion_429_and_5xx_stop_but_401_is_candidate_failure(self):
        for status in (429, 500, 503, 599, 401):
            with self.subTest(status=status):
                wire_count = 0

                async def status_response(request: httpx.Request) -> httpx.Response:
                    nonlocal wire_count
                    wire_count += 1
                    return httpx.Response(
                        status,
                        request=request,
                        json={"error": {"message": "private response detail"}},
                    )

                async def count_tokens(
                    _model: str, _messages: list[dict[str, str]]
                ) -> int:
                    return 100

                observer = H0WireObserver()
                openai_client = build_h0_openai_client(
                    api_key="offline-test-key",
                    base_url="http://offline.invalid/v1",
                    observer=observer,
                    transport=httpx.MockTransport(status_response),
                )
                ledger = H0AttemptLedger(stage_attempt_id=f"offline-status-{status}")
                client = H0QwenVLLMClient(
                    config=LLMConfig(
                        api_key="unused",
                        model="qwen3-32b-fp8",
                        small_model="qwen3-32b-fp8",
                        base_url="http://offline.invalid/v1",
                        temperature=0.0,
                        max_tokens=16384,
                    ),
                    candidate=self._candidate("Q1"),
                    token_counter=count_tokens,
                    semantic_guardrail=H0SemanticUtilityTests()._guardrail(),
                    ledger=ledger,
                    client=openai_client,
                )
                try:
                    expected = (
                        H0QualificationError if status == 401 else H0InfrastructureError
                    )
                    with self.assertRaises(expected):
                        with episode_scope("offline-run", 0):
                            await client.generate_response(
                                [
                                    Message(role="system", content="s"),
                                    Message(role="user", content="u"),
                                ],
                                response_model=ExtractedEntities,
                                group_id="07741c45",
                                prompt_name="extract_nodes.extract_message",
                            )
                finally:
                    await openai_client.close()

                self.assertEqual(wire_count, 1)
                self.assertEqual(len(ledger.attempts), 1)
                expected_failure = (
                    "completion_transport_failure"
                    if status == 401
                    else "vllm_unreachable"
                )
                self.assertEqual(ledger.attempts[0]["failure_class"], expected_failure)
                persisted = json.dumps(ledger.safe_artifact(), sort_keys=True)
                self.assertNotIn("private response detail", persisted)


class H0ExactTokenCounterTests(IsolatedAsyncioTestCase):
    async def test_vllm_tokenize_uses_final_messages_and_persists_only_safe_evidence(self):
        bodies: list[dict[str, object]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/tokenize")
            self.assertEqual(request.headers["Authorization"], "Bearer offline-key")
            bodies.append(json.loads(request.content))
            return httpx.Response(
                200,
                request=request,
                json={"count": 321, "max_model_len": 40960, "token_ids": [1, 2]},
            )

        counter = VLLMChatTokenCounter(
            base_url="http://offline.invalid/v1/",
            model="qwen3-32b-fp8",
            api_key="offline-key",
            transport=httpx.MockTransport(handler),
        )
        messages = [
            {"role": "system", "content": "TOKEN_COUNTER_RAW_SENTINEL"},
            {"role": "user", "content": "user"},
        ]
        try:
            count = await counter("qwen3-32b-fp8", messages)
        finally:
            await counter.close()

        self.assertEqual(count, 321)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["messages"], messages)
        self.assertEqual(
            bodies[0]["chat_template_kwargs"], {"enable_thinking": False}
        )
        self.assertFalse(bodies[0]["add_special_tokens"])
        persisted = json.dumps(counter.events, sort_keys=True)
        self.assertNotIn("TOKEN_COUNTER_RAW_SENTINEL", persisted)
        self.assertNotIn("offline-key", persisted)
        self.assertNotIn("Authorization", persisted)
        self.assertEqual(counter.events[0]["prompt_tokens"], 321)
        self.assertRegex(counter.events[0]["request_sha256"], r"^[0-9a-f]{64}$")

    async def test_tokenize_connection_failure_is_stop_and_report(self):
        async def unavailable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated", request=request)

        counter = VLLMChatTokenCounter(
            base_url="http://offline.invalid/v1",
            model="qwen3-32b-fp8",
            api_key="offline-key",
            transport=httpx.MockTransport(unavailable),
        )
        try:
            with self.assertRaisesRegex(H0InfrastructureError, "vllm_unreachable"):
                await counter(
                    "qwen3-32b-fp8",
                    [{"role": "user", "content": "raw must not survive"}],
                )
        finally:
            await counter.close()
        self.assertEqual(counter.events[-1]["failure_class"], "vllm_unreachable")
        self.assertNotIn("simulated", json.dumps(counter.events))

    async def test_tokenize_any_httpx_transport_error_is_stop_and_report(self):
        async def unavailable(request: httpx.Request) -> httpx.Response:
            raise httpx.TransportError("private generic transport detail", request=request)

        counter = VLLMChatTokenCounter(
            base_url="http://offline.invalid/v1",
            model="qwen3-32b-fp8",
            api_key="offline-key",
            transport=httpx.MockTransport(unavailable),
        )
        try:
            with self.assertRaisesRegex(H0InfrastructureError, "vllm_unreachable"):
                await counter(
                    "qwen3-32b-fp8",
                    [{"role": "user", "content": "raw transport prompt"}],
                )
        finally:
            await counter.close()
        self.assertEqual(counter.events[-1]["failure_class"], "vllm_unreachable")
        persisted = json.dumps(counter.events, sort_keys=True)
        self.assertNotIn("private generic transport detail", persisted)
        self.assertNotIn("raw transport prompt", persisted)

    async def test_tokenize_429_and_5xx_are_infrastructure_failures(self):
        for status in (429, 500, 503, 599):
            with self.subTest(status=status):
                async def unavailable(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(
                        status,
                        request=request,
                        content=b"private service response",
                    )

                counter = VLLMChatTokenCounter(
                    base_url="http://offline.invalid/v1",
                    model="qwen3-32b-fp8",
                    api_key="offline-key",
                    transport=httpx.MockTransport(unavailable),
                )
                try:
                    with self.assertRaisesRegex(
                        H0InfrastructureError, "vllm_unreachable"
                    ):
                        await counter(
                            "qwen3-32b-fp8",
                            [{"role": "user", "content": "raw status prompt"}],
                        )
                finally:
                    await counter.close()
                self.assertEqual(counter.events[-1]["http_status"], status)
                self.assertEqual(
                    counter.events[-1]["failure_class"], "vllm_unreachable"
                )
                persisted = json.dumps(counter.events, sort_keys=True)
                self.assertNotIn("private service response", persisted)
                self.assertNotIn("raw status prompt", persisted)

    async def test_tokenize_non_infrastructure_http_error_is_candidate_failure(self):
        async def unauthorized(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, request=request, content=b"private auth response")

        counter = VLLMChatTokenCounter(
            base_url="http://offline.invalid/v1",
            model="qwen3-32b-fp8",
            api_key="offline-key",
            transport=httpx.MockTransport(unauthorized),
        )
        try:
            with self.assertRaisesRegex(H0QualificationError, "tokenize_http_failure"):
                await counter(
                    "qwen3-32b-fp8",
                    [{"role": "user", "content": "raw auth prompt"}],
                )
        finally:
            await counter.close()
        self.assertEqual(counter.events[-1]["http_status"], 401)
        self.assertEqual(counter.events[-1]["failure_class"], "tokenize_http_failure")
        persisted = json.dumps(counter.events, sort_keys=True)
        self.assertNotIn("private auth response", persisted)
        self.assertNotIn("raw auth prompt", persisted)


class H0CheckpointTests(TestCase):
    def _h0_b_harness_repair_admission(
        self,
        *,
        failed_attempt_id: str,
        failed_checkpoint_sha256: str,
        replacement_attempt_id: str,
    ) -> dict[str, object]:
        return {
            "schema_version": "membind.h0.harness-repair-admission.v1",
            "protocol_version": "current-validation-v1.3",
            "candidate_id": "Q1",
            "phase": "H0-B",
            "decision_path": "artifacts/h0_protocol_repair/decisions/h0-b.json",
            "decision_sha256": "1" * 64,
            "decision_result_blind": False,
            "prior_model_workload_output_observed": False,
            "repair_required_independent_of_model_output": True,
            "scientific_configuration_unchanged": True,
            "one_shot_whole_stage_replacement": True,
            "replacement_attempt_id": replacement_attempt_id,
            "invalidated_stage_attempt_id": failed_attempt_id,
            "invalidated_checkpoint_index_sha256": failed_checkpoint_sha256,
            "failure_report_sha256": "2" * 64,
            "old_attempt_qualification_reusable": False,
            "old_and_new_trial_counts_mergeable": False,
            "prior_manifest_index_sha256": "3" * 64,
            "repaired_manifest_index_sha256": "4" * 64,
            "secrets_persisted": False,
        }

    def test_h0_b_harness_repair_admits_one_exact_whole_stage_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            failed_id = "h0-q1-b-harness-failed-001"
            replacement_id = "h0-q1-b-harness-replacement-001"
            failed = H0CheckpointStore(
                root=root,
                stage_attempt_id=failed_id,
                candidate_id="Q1",
                phase="H0-B",
            )
            failed.mark_candidate_failure("manifest_contract_failure", "a" * 64)
            failed_sha = sha256_file(failed.index_path)
            admission = self._h0_b_harness_repair_admission(
                failed_attempt_id=failed_id,
                failed_checkpoint_sha256=failed_sha,
                replacement_attempt_id=replacement_id,
            )

            replacement = H0CheckpointStore(
                root=root,
                stage_attempt_id=replacement_id,
                candidate_id="Q1",
                phase="H0-B",
                repair_admission=admission,
            )

            self.assertEqual(replacement.index["prior_matching_attempt_count"], 1)
            self.assertEqual(
                replacement.index["infrastructure_interrupted_attempt_count"], 0
            )
            self.assertTrue(replacement.index["whole_stage_rerun"])
            self.assertTrue(replacement.index["protocol_repair_replacement"])
            self.assertTrue(replacement.index["harness_repair_replacement"])
            self.assertEqual(replacement.index["repair_admission"], admission)

    def test_h0_b_harness_repair_rejects_wrong_or_reusable_binding(self):
        mutations = (
            {"replacement_attempt_id": "another-attempt"},
            {"invalidated_checkpoint_index_sha256": "0" * 64},
            {"phase": "H0-A"},
            {"decision_result_blind": True},
            {"prior_model_workload_output_observed": True},
            {"repair_required_independent_of_model_output": False},
            {"scientific_configuration_unchanged": False},
            {"one_shot_whole_stage_replacement": False},
            {"old_attempt_qualification_reusable": True},
            {"old_and_new_trial_counts_mergeable": True},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                failed_id = "h0-q1-b-harness-failed-001"
                replacement_id = "h0-q1-b-harness-replacement-001"
                failed = H0CheckpointStore(
                    root=root,
                    stage_attempt_id=failed_id,
                    candidate_id="Q1",
                    phase="H0-B",
                )
                failed.mark_candidate_failure(
                    "manifest_contract_failure", "a" * 64
                )
                admission = self._h0_b_harness_repair_admission(
                    failed_attempt_id=failed_id,
                    failed_checkpoint_sha256=sha256_file(failed.index_path),
                    replacement_attempt_id=replacement_id,
                )
                admission.update(mutation)

                with self.assertRaises(H0StateGateError):
                    H0CheckpointStore(
                        root=root,
                        stage_attempt_id=replacement_id,
                        candidate_id="Q1",
                        phase="H0-B",
                        repair_admission=admission,
                    )

    def test_h0_b_harness_repair_reopen_rejects_tampered_admission(self):
        mutations = (
            {"prior_model_workload_output_observed": True},
            {"scientific_configuration_unchanged": False},
            {"old_attempt_qualification_reusable": True},
            {"unexpected_field": "not-admitted"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                failed_id = "h0-q1-b-harness-failed-001"
                replacement_id = "h0-q1-b-harness-replacement-001"
                failed = H0CheckpointStore(
                    root=root,
                    stage_attempt_id=failed_id,
                    candidate_id="Q1",
                    phase="H0-B",
                )
                failed.mark_candidate_failure(
                    "manifest_contract_failure", "a" * 64
                )
                admission = self._h0_b_harness_repair_admission(
                    failed_attempt_id=failed_id,
                    failed_checkpoint_sha256=sha256_file(failed.index_path),
                    replacement_attempt_id=replacement_id,
                )
                replacement = H0CheckpointStore(
                    root=root,
                    stage_attempt_id=replacement_id,
                    candidate_id="Q1",
                    phase="H0-B",
                    repair_admission=admission,
                )
                changed = deepcopy(replacement.index)
                changed["repair_admission"].update(mutation)
                replacement.index_path.write_bytes(canonical_json_bytes(changed))

                with self.assertRaises(H0ManifestError):
                    H0CheckpointStore.open_existing(root, replacement_id)

    def test_new_attempt_is_admitted_only_after_infrastructure_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            interrupted = H0CheckpointStore(
                root=root,
                stage_attempt_id="infra-attempt-001",
                candidate_id="Q1",
                phase="H0-A",
            )
            interrupted.mark_infrastructure_interruption("vllm_unreachable")
            rerun = H0CheckpointStore(
                root=root,
                stage_attempt_id="infra-attempt-002",
                candidate_id="Q1",
                phase="H0-A",
            )
            self.assertEqual(rerun.index["prior_matching_attempt_count"], 1)
            self.assertEqual(
                rerun.index["infrastructure_interrupted_attempt_count"], 1
            )
            self.assertTrue(rerun.index["whole_stage_rerun"])

        for terminal in ("stage_complete", "candidate_failed", "running"):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prior = H0CheckpointStore(
                    root=root,
                    stage_attempt_id=f"prior-{terminal}",
                    candidate_id="Q1",
                    phase="H0-A",
                )
                if terminal == "stage_complete":
                    prior.mark_stage_complete("a" * 64)
                elif terminal == "candidate_failed":
                    prior.mark_candidate_failure("schema_failure", "b" * 64)

                with self.assertRaises(H0StateGateError):
                    H0CheckpointStore(
                        root=root,
                        stage_attempt_id=f"second-{terminal}",
                        candidate_id="Q1",
                        phase="H0-A",
                    )

    def test_terminal_transitions_bind_results_and_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            complete = H0CheckpointStore(
                root=root,
                stage_attempt_id="complete-attempt",
                candidate_id="Q1",
                phase="H0-A",
            )
            complete.record_segment(
                "stage_result",
                "result",
                {"qualified": True, "result_sha256": "a" * 64},
            )
            completed = complete.mark_stage_complete("a" * 64)
            self.assertEqual(completed["status"], "stage_complete")
            self.assertTrue(completed["candidate_advance_allowed"])
            self.assertTrue(completed["partial_qualification_reusable"])
            self.assertFalse(completed["requires_whole_stage_rerun"])
            self.assertEqual(completed["terminal_result_sha256"], "a" * 64)
            with self.assertRaises(RuntimeError):
                complete.record_segment("late", "late", {"qualified": True})
            with self.assertRaises(RuntimeError):
                complete.mark_candidate_failure("schema_failure", "b" * 64)

            failed_root = root / "independent-failure-stage"
            failed = H0CheckpointStore(
                root=failed_root,
                stage_attempt_id="candidate-failure-attempt",
                candidate_id="Q1",
                phase="H0-A",
            )
            failed.record_segment(
                "candidate_failure",
                "failure",
                {"failure_code": "schema_failure", "evidence_sha256": "b" * 64},
            )
            failure = failed.mark_candidate_failure("schema_failure", "b" * 64)
            self.assertEqual(failure["status"], "candidate_failed")
            self.assertFalse(failure["candidate_advance_allowed"])
            self.assertTrue(failure["candidate_selection_may_continue"])
            self.assertFalse(failure["partial_qualification_reusable"])
            self.assertFalse(failure["requires_whole_stage_rerun"])
            self.assertEqual(failure["failure_evidence_sha256"], "b" * 64)
            with self.assertRaises(RuntimeError):
                failed.mark_infrastructure_interruption("vllm_unreachable")

            reopened_complete = H0CheckpointStore.open_existing(
                root, "complete-attempt"
            )
            reopened_failed = H0CheckpointStore.open_existing(
                failed_root, "candidate-failure-attempt"
            )
            self.assertEqual(reopened_complete.index["status"], "stage_complete")
            self.assertEqual(reopened_failed.index["status"], "candidate_failed")

    def test_terminal_transition_rejects_invalid_hashes_and_failure_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = H0CheckpointStore(
                root=root,
                stage_attempt_id="invalid-terminal-attempt",
                candidate_id="Q1",
                phase="H0-A",
            )
            with self.assertRaises(ValueError):
                store.mark_stage_complete("not-a-hash")
            for code in ("", "contains spaces", "path/escape"):
                with self.subTest(code=code):
                    with self.assertRaises(ValueError):
                        store.mark_candidate_failure(code, "c" * 64)

    def test_segments_are_content_addressed_and_infra_stop_preserves_partial_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            progress: list[dict[str, object]] = []
            store = H0CheckpointStore(
                root=Path(tmp),
                stage_attempt_id="h0_q1_a_attempt_001",
                candidate_id="Q1",
                phase="H0-A",
                progress_sink=progress.append,
            )
            first = store.record_segment(
                segment_kind="logical_trial",
                segment_id="trial-000",
                payload={"qualified": True, "response_sha256": "a" * 64},
            )
            second = store.record_segment(
                segment_kind="logical_trial",
                segment_id="trial-001",
                payload={"qualified": True, "response_sha256": "b" * 64},
            )
            stopped = store.mark_infrastructure_interruption("vllm_unreachable")

            self.assertNotEqual(first["artifact_sha256"], second["artifact_sha256"])
            self.assertTrue(Path(tmp, first["artifact_path"]).is_file())
            self.assertTrue(Path(tmp, second["artifact_path"]).is_file())
            self.assertEqual(stopped["status"], "infrastructure_interrupted")
            self.assertTrue(stopped["partial_evidence_preserved"])
            self.assertTrue(stopped["requires_whole_stage_rerun"])
            self.assertFalse(stopped["partial_qualification_reusable"])
            self.assertGreaterEqual(len(progress), 3)
            reopened = H0CheckpointStore.open_existing(Path(tmp), "h0_q1_a_attempt_001")
            self.assertEqual(len(reopened.index["segments"]), 2)
            self.assertEqual(reopened.index["status"], "infrastructure_interrupted")

    def test_checkpoint_rejects_raw_or_secret_bearing_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = H0CheckpointStore(
                root=Path(tmp),
                stage_attempt_id="safe-attempt",
                candidate_id="Q1",
                phase="H0-A",
            )
            for payload in (
                {"raw_response": "secret"},
                {"messages": [{"content": "prompt"}]},
                {"Authorization": "Bearer secret"},
                {"api_key": "secret"},
                {"env_dump": {"SAFE_LOOKING_NAME": "secret"}},
                {"note": "Bearer secret"},
            ):
                with self.subTest(payload=payload):
                    with self.assertRaises(ValueError):
                        store.record_segment("logical_trial", "unsafe", payload)

    def test_checkpoint_rejects_attempt_path_escape_and_existing_attempt_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                H0CheckpointStore(
                    root=root,
                    stage_attempt_id="../escape",
                    candidate_id="Q1",
                    phase="H0-A",
                )

            store = H0CheckpointStore(
                root=root,
                stage_attempt_id="immutable-attempt",
                candidate_id="Q1",
                phase="H0-A",
            )
            store.record_segment(
                "logical_trial",
                "trial-000",
                {"qualified": True, "response_sha256": "a" * 64},
            )
            with self.assertRaises(
                (FileExistsError, H0ManifestError, H0StateGateError)
            ):
                H0CheckpointStore(
                    root=root,
                    stage_attempt_id="immutable-attempt",
                    candidate_id="Q1",
                    phase="H0-A",
                )
            reopened = H0CheckpointStore.open_existing(root, "immutable-attempt")
            self.assertEqual(len(reopened.index["segments"]), 1)

    def test_open_existing_rejects_tampered_content_addressed_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = H0CheckpointStore(
                root=root,
                stage_attempt_id="tamper-check",
                candidate_id="Q1",
                phase="H0-A",
            )
            persisted = store.record_segment(
                "logical_trial",
                "trial-000",
                {"qualified": True, "response_sha256": "a" * 64},
            )
            artifact = root / str(persisted["artifact_path"])
            artifact.write_text("{}", encoding="utf-8")

            with self.assertRaises(H0ManifestError):
                H0CheckpointStore.open_existing(root, "tamper-check")


class H0CheckpointPlanContractTests(TestCase):
    _MACHINE_CONTRACTS = (
        "checkpoint_granularity_H0_A: per_logical_trial",
        "checkpoint_granularity_H0_B_C: per_source_sequence",
        "checkpoint_payload: sanitized_detailed_ledger_counts_hashes_failure_codes",
        "partial_evidence_preserved_on_interruption: true",
        "partial_qualification_reusable_after_infra_failure: false",
        "whole_affected_stage_rerun_with_new_attempt_id: true",
        "vllm_connectivity_failure: stop_and_report",
        "automatic_candidate_advance_after_connectivity_failure: false",
    )

    def test_current_plan_freezes_segmented_output_and_connection_stop_policy(self):
        text = (ROOT.parent / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md").read_text(
            encoding="utf-8"
        )
        for contract in self._MACHINE_CONTRACTS:
            self.assertIn(contract, text)

    def test_proposal_execution_plan_and_memory_preserve_checkpoint_semantics(self):
        proposal = (ROOT.parent / "MemBind_basic_validation_experiment.md").read_text(
            encoding="utf-8"
        )
        execution_plan = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")
        memory = (ROOT / "GLOBAL_MEMORY.md").read_text(encoding="utf-8")

        for contract in self._MACHINE_CONTRACTS:
            self.assertIn(contract, execution_plan)
        for phrase in (
            "H0-A \u5728\u6bcf\u4e2a logical trial \u5b8c\u6210\u540e",
            "H0-B/H0-C \u5728\u6bcf\u4e2a `source_sequence` \u5b8c\u6210\u540e",
            "\u4e0d\u5f97\u4e0e\u6062\u590d\u540e\u7684\u8865\u8dd1\u62fc\u63a5\u4e3a",
            "vLLM \u8fde\u63a5\u5931\u8d25",
        ):
            self.assertIn(phrase, proposal)
        for phrase in (
            "never for qualification reuse",
            "whole affected H0 stage",
            "vLLM connectivity failure means stop-and-report",
        ):
            self.assertIn(phrase, memory)

    def test_h0_execution_ambiguities_are_frozen_before_live_output(self):
        current = (ROOT.parent / "MemBind_CURRENT_VALIDATION_PLAN_v1.3.md").read_text(
            encoding="utf-8"
        )
        execution = (ROOT / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")
        contracts = (
            "h0_a_execution_unit: direct_extract_nodes_public_call",
            "h0_a_db_and_embedding_calls: zero",
            "h0_a_client_lifecycle: fresh_per_repeated_trial_shared_stage_ledger",
            "h0_b_c_graph_isolation: fresh_asserted_clean_graph_per_history",
            "h0_qualification_llm_warmup_calls: forbidden",
            "canonical_graph_nonempty_definition: entity_count_gt_zero",
            "full_history_source_mapping: exact_episodic_set_and_resolved_edge_attribution",
            "evidence_recall_at_10_definition: first_10_unique_session_ids_from_at_most_10_rrf_edges",
            "h0_c_infrastructure_rerun_scope: all_three_histories_new_attempt_id",
        )
        for contract in contracts:
            self.assertIn(contract, current)
            self.assertIn(contract, execution)


class H0LedgerTests(TestCase):
    def _request_evidence(self) -> dict[str, object]:
        candidate = H0BudgetAndSchemaTests()._candidate("Q1")
        prepared = prepare_h0_prompt(
            [SimpleNamespace(role="user", content="ledger payload")],
            ExtractedEntities,
            "json_schema",
        )
        return build_h0_completion_request(candidate, prepared, prompt_tokens=100).evidence

    def test_retry_is_same_logical_trial_and_disqualifies_later_success(self):
        ledger = H0AttemptLedger(stage_attempt_id="offline-stage-001")
        logical_id = ledger.start_trial(
            candidate_id="Q1",
            call_key="07741c45:0:extract_nodes.extract_message",
            repeated_trial_index=0,
        )
        first = ledger.start_attempt(logical_id, self._request_evidence())
        ledger.finish_attempt(
            first,
            http_status=200,
            finish_reason="length",
            response_text='{"cut":',
            response_prompt_tokens=100,
            json_parse_success=False,
            pydantic_validation_success=False,
            semantic_utility_success=False,
        )
        second = ledger.start_attempt(logical_id, self._request_evidence())
        ledger.finish_attempt(
            second,
            http_status=200,
            finish_reason="stop",
            response_text='{"extracted_entities":[{"name":"A","episode_indices":[0]}]}',
            response_prompt_tokens=100,
            json_parse_success=True,
            pydantic_validation_success=True,
            semantic_utility_success=True,
        )

        self.assertEqual(ledger.attempts[0]["retry_index"], 0)
        self.assertEqual(ledger.attempts[1]["retry_index"], 1)
        self.assertEqual(ledger.attempts[0]["logical_trial_id"], logical_id)
        self.assertEqual(ledger.attempts[1]["logical_trial_id"], logical_id)
        self.assertNotEqual(
            ledger.attempts[0]["http_attempt_id"],
            ledger.attempts[1]["http_attempt_id"],
        )
        verdict = ledger.trial_verdict(logical_id)
        self.assertFalse(verdict["qualified"])
        self.assertIn("candidate_induced_retry", verdict["failure_reasons"])

        persisted = json.dumps(ledger.safe_artifact(), sort_keys=True)
        self.assertNotIn('"raw_response"', persisted)
        self.assertNotIn('"messages"', persisted)
        self.assertNotIn('"Authorization"', persisted)
        self.assertNotIn('"name":"A"', persisted.replace(" ", ""))

    def test_server_prompt_token_mismatch_is_manifest_failure(self):
        ledger = H0AttemptLedger(stage_attempt_id="offline-stage-002")
        logical_id = ledger.start_trial("Q1", "call", 0)
        attempt_id = ledger.start_attempt(logical_id, self._request_evidence())
        ledger.finish_attempt(
            attempt_id,
            http_status=200,
            finish_reason="stop",
            response_text='{"extracted_entities":[]}',
            response_prompt_tokens=101,
            json_parse_success=True,
            pydantic_validation_success=True,
            semantic_utility_success=False,
        )
        verdict = ledger.trial_verdict(logical_id)
        self.assertFalse(verdict["qualified"])
        self.assertIn("prompt_token_count_mismatch", verdict["failure_reasons"])


class H0DataScopeTests(TestCase):
    def _corpus(self, directory: str):
        source = Path(directory) / "source.json"
        records = [
            {
                "question_id": question_id,
                "haystack_sessions": [[{"role": "user", "content": question_id}]],
                "haystack_dates": ["2025/01/01 (Wed) 00:00"],
            }
            for question_id in ("07741c45", "b6019101", "eval", "quarantine")
        ]
        source.write_text(json.dumps(records), encoding="utf-8")
        import hashlib

        split = Path(directory) / "split.json"
        split.write_text(
            json.dumps(
                {
                    "protocol_version": "current-validation-v1.3",
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "calibration_question_ids": ["07741c45", "b6019101"],
                    "compatibility_development_question_ids": ["quarantine"],
                    "evaluation_question_ids": ["eval"],
                }
            ),
            encoding="utf-8",
        )
        return load_h0_calibration_corpus(split, source)

    def test_corpus_exposes_only_calibration_and_rejects_before_service_factory(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = self._corpus(tmp)
            self.assertEqual(corpus.question_ids, ("07741c45", "b6019101"))
            factory = Mock(side_effect=AssertionError("must reject before service access"))
            for question_id in ("eval", "quarantine", "unknown"):
                with self.subTest(question_id=question_id):
                    with self.assertRaises(H0DataScopeError):
                        enter_h0_case(corpus, question_id, factory)
            factory.assert_not_called()

    def test_h0_workload_is_calibration_only_and_h0_a_is_three_repeated_trials(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = self._corpus(tmp)
            h0_a = build_h0_workload(corpus, "H0-A")
            self.assertEqual(len(h0_a), 3)
            self.assertEqual({item.question_id for item in h0_a}, {"07741c45"})
            self.assertEqual({item.source_sequence for item in h0_a}, {0})
            self.assertEqual([item.repeated_trial_index for item in h0_a], [0, 1, 2])
            self.assertTrue(all(not item.statistically_independent for item in h0_a))
            h0_b = build_h0_workload(corpus, "H0-B")
            h0_c = build_h0_workload(corpus, "H0-C")
            self.assertEqual({item.question_id for item in h0_b}, {"07741c45"})
            self.assertEqual({item.question_id for item in h0_c}, {"b6019101"})


class H0SemanticUtilityTests(TestCase):
    def _guardrail(self) -> dict[str, object]:
        return {
            "expected_nonempty_call_ids": {
                "07741c45:0:extract_nodes.extract_message": {
                    "minimum_entity_count": 1,
                    "minimum_distinct_normalized_entity_name_count": 1,
                },
                "b6019101:0:extract_nodes.extract_message": {
                    "minimum_entity_count": 1,
                    "minimum_distinct_normalized_entity_name_count": 1,
                },
            },
            "normalization": "NFKC_strip_collapse_whitespace_casefold_v1",
            "expected_episode_indices": [0],
            "cross_call_constant_detection_groups": [
                [
                    "07741c45:0:extract_nodes.extract_message",
                    "b6019101:0:extract_nodes.extract_message",
                ]
            ],
        }

    def test_pydantic_valid_empty_blank_duplicate_or_defaulted_output_fails(self):
        guardrail = self._guardrail()
        call = "07741c45:0:extract_nodes.extract_message"
        invalid = (
            {"extracted_entities": []},
            {"extracted_entities": [{"name": " ", "episode_indices": [0]}]},
            {
                "extracted_entities": [
                    {"name": "Alpha", "episode_indices": [0]},
                    {"name": " alpha ", "episode_indices": [0]},
                ]
            },
            {"extracted_entities": [{"name": "Alpha"}]},
            {"extracted_entities": [{"name": "Alpha", "episode_indices": [1]}]},
        )
        for parsed in invalid:
            with self.subTest(parsed=parsed):
                ExtractedEntities(**parsed)
                with self.assertRaises(H0SemanticError):
                    evaluate_semantic_call(
                        guardrail,
                        call_key=call,
                        response_model_name="ExtractedEntities",
                        parsed=parsed,
                    )

    def test_semantic_artifact_contains_only_counts_hashes_and_failure_codes(self):
        result = evaluate_semantic_call(
            self._guardrail(),
            call_key="07741c45:0:extract_nodes.extract_message",
            response_model_name="ExtractedEntities",
            parsed={
                "extracted_entities": [
                    {"name": "Private Entity Name", "episode_indices": [0]}
                ]
            },
        )
        persisted = json.dumps(result, sort_keys=True)
        self.assertNotIn("Private Entity Name", persisted)
        self.assertEqual(result["entity_count"], 1)
        self.assertRegex(result["semantic_payload_sha256"], r"^[0-9a-f]{64}$")

    def test_frozen_forbidden_default_payload_hash_is_enforced(self):
        parsed = {
            "extracted_entities": [
                {"name": "Schema Default", "episode_indices": [0]}
            ]
        }
        guardrail = self._guardrail()
        guardrail["forbidden_default_payload_sha256"] = [
            canonical_json_sha256(parsed)
        ]

        with self.assertRaisesRegex(H0SemanticError, "schema_default"):
            evaluate_semantic_call(
                guardrail,
                call_key="unregistered:0:extract_nodes.extract_message",
                response_model_name="ExtractedEntities",
                parsed=parsed,
            )

    def test_constant_payload_across_distinct_calls_fails_but_repeated_trial_does_not(self):
        guardrail = self._guardrail()
        payload = {
            "extracted_entities": [{"name": "Same", "episode_indices": [0]}]
        }
        first = evaluate_semantic_call(
            guardrail,
            "07741c45:0:extract_nodes.extract_message",
            "ExtractedEntities",
            payload,
        )
        repeated = dict(first, repeated_trial_index=1)
        self.assertTrue(validate_semantic_stage(guardrail, [first, repeated])["qualified"])
        second = evaluate_semantic_call(
            guardrail,
            "b6019101:0:extract_nodes.extract_message",
            "ExtractedEntities",
            payload,
        )
        with self.assertRaisesRegex(H0SemanticError, "constant"):
            validate_semantic_stage(guardrail, [first, second])


class H0RegistryAndSelectionTests(TestCase):
    def test_registry_is_content_addressed_and_ordered(self):
        registry = load_h0_registry(ROOT)
        self.assertEqual(registry.candidate_ids, ("Q1", "Q2", "Q3"))
        self.assertEqual(registry.base_spec["unresolved_fields"], list(registry.unresolved_fields))
        self.assertTrue(all(not item.spec["live_eligible"] for item in registry.candidates))

    def test_registry_resolution_fails_closed_without_every_binding(self):
        registry = load_h0_registry(ROOT)
        with self.assertRaisesRegex(H0ManifestError, "unresolved artifact bindings"):
            registry.resolve({})

    def test_first_pass_stops_later_candidates_and_performance_never_selects(self):
        called: list[str] = []

        def execute(candidate_id: str) -> H0CandidateResult:
            called.append(candidate_id)
            if candidate_id == "Q1":
                return H0CandidateResult(candidate_id, qualified=False)
            return H0CandidateResult(candidate_id, qualified=True)

        result = run_first_passing_candidates(("Q1", "Q2", "Q3"), execute)
        self.assertEqual(called, ["Q1", "Q2"])
        self.assertEqual(result.selected_candidate_id, "Q2")
        self.assertEqual(result.outcomes["Q3"], "not_executed_first_pass_selected")

    def test_shared_invariant_failure_skips_remaining_candidates_without_calls(self):
        called: list[str] = []

        def execute(candidate_id: str) -> H0CandidateResult:
            called.append(candidate_id)
            return H0CandidateResult(
                candidate_id,
                qualified=False,
                shared_invariant_failure=True,
            )

        result = run_first_passing_candidates(("Q1", "Q2", "Q3"), execute)
        self.assertEqual(called, ["Q1"])
        self.assertEqual(
            result.terminal_status,
            "H0_BLOCKED_ALL_PREREGISTERED_CANDIDATES_FAILED",
        )
        self.assertEqual(
            result.outcomes["Q2"], "not_executed_shared_invariant_failure"
        )
        self.assertEqual(
            result.outcomes["Q3"], "not_executed_shared_invariant_failure"
        )

    def test_malformed_or_incomplete_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CURRENT_STATE.json"
            for payload in (
                "not-json",
                json.dumps({}),
                json.dumps(
                    {
                        "protocol_version": "current-validation-v1.3",
                        "current_stage": "H0",
                        "status": "h0_q1_a_live_only",
                        "current_action_scope": "h0_q1_a_live_only",
                        "live_h0_candidate_authorized": True,
                    }
                ),
            ):
                with self.subTest(payload=payload):
                    path.write_text(payload, encoding="utf-8")
                    with self.assertRaises(H0StateGateError):
                        enter_h0_runtime(
                            state_path=path,
                            candidate_id="Q1",
                            phase="H0-A",
                            env_loader=Mock(),
                            service_factory=Mock(),
                        )


if __name__ == "__main__":
    import unittest

    unittest.main()
