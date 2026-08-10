"""Offline integration contracts for the Protocol v1.3 H0 phase runner.

The fakes deliberately expose private graph and model values so these tests can
prove that the runner returns and checkpoints only counts, hashes, identifiers,
and qualification flags.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import h0_phase_runner as phase  # noqa: E402
from dataset import Episode  # noqa: E402
from h0_runtime import (  # noqa: E402
    H0AttemptLedger,
    H0InfrastructureError,
    H0ManifestError,
    H0QualificationError,
    H0SemanticError,
)
from instrumentation import current_episode_key  # noqa: E402


@lru_cache(maxsize=1)
def _frozen_primary_record() -> dict:
    manifest = json.loads(
        (ROOT / "artifacts/dataset/frozen_split_v1_3.json").read_text(encoding="utf-8")
    )
    source = Path(manifest["source_path"])
    records = json.loads(source.read_text(encoding="utf-8"))
    return next(record for record in records if record.get("question_id") == "07741c45")


def _episode(sequence: int) -> Episode:
    return Episode(
        question_id="history-a",
        group_id="history-a",
        session_id=f"private-session-{sequence}",
        source_sequence=sequence,
        source_hash=f"{sequence + 11:064x}",
        reference_time=f"2026-01-{sequence + 1:02d}T00:00:00Z",
        body=f"private episode body {sequence}",
    )


def _history_episode(question_id: str, sequence: int) -> Episode:
    return Episode(
        question_id=question_id,
        group_id=question_id,
        session_id=f"private-{question_id}-session-{sequence}",
        source_sequence=sequence,
        source_hash=f"{sequence + len(question_id) + 211:064x}",
        reference_time=f"2026-02-{sequence % 28 + 1:02d}T00:00:00Z",
        body=f"private {question_id} episode {sequence}",
    )


def _calibration_corpus() -> SimpleNamespace:
    counts = {
        "07741c45": 49,
        "b6019101": 49,
        "6071bd76": 46,
        "a2f3aa27": 44,
    }
    ids = tuple(counts)
    return SimpleNamespace(
        question_ids=ids,
        records={
            question_id: {
                "question_id": question_id,
                "answer_session_ids": [f"private-{question_id}-session-0"],
            }
            for question_id in ids
        },
        episodes={
            question_id: tuple(
                _history_episode(question_id, sequence) for sequence in range(count)
            )
            for question_id, count in counts.items()
        },
    )


def _semantic_record(sequence: int, *, call_key: str | None = None) -> dict:
    return {
        "call_key": call_key or f"history-a:{sequence}:extract_nodes.extract_message",
        "response_model_name": "ExtractedEntities",
        "entity_count": 1,
        "distinct_normalized_entity_name_count": 1,
        "semantic_payload_sha256": f"{sequence + 101:064x}",
        "failure_codes": [],
        "qualified": True,
        "repeated_trial_index": 0,
    }


def _record_qualified_trial(
    ledger: H0AttemptLedger,
    *,
    call_key: str,
    repeated_trial_index: int,
) -> str:
    logical_id = ledger.start_trial("Q1", call_key, repeated_trial_index)
    request = {
        "model": "qwen3-32b-fp8",
        "temperature": 0.0,
        "top_p": 1.0,
        "top_k": None,
        "min_p": None,
        "max_tokens": 10,
        "prompt_tokens": 5,
        "server_request_seed": 20260806,
        "requested_request_payload_sha256": "a" * 64,
    }
    attempt_id = ledger.start_attempt(logical_id, request)
    ledger.attach_observed_request(
        attempt_id,
        {
            **request,
            "observed_request_payload_sha256": "b" * 64,
            "response_format_sha256": "c" * 64,
            "message_content_sha256": ["d" * 64],
        },
    )
    ledger.finish_attempt(
        attempt_id,
        http_status=200,
        finish_reason="stop",
        response_text='{"ok":true}',
        response_prompt_tokens=5,
        json_parse_success=True,
        pydantic_validation_success=True,
        semantic_utility_success=True,
    )
    return logical_id


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        if self.closed:
            raise AssertionError("resource closed more than once")
        self.closed = True


def _graph_with_ledger(ledger: H0AttemptLedger) -> SimpleNamespace:
    llm_client = SimpleNamespace(h0_ledger=ledger)
    return SimpleNamespace(llm_client=llm_client, clients=SimpleNamespace(llm_client=llm_client))


def _valid_graph(episodes: list[Episode]) -> dict:
    return {
        "entities": [{"name": "private entity name"}],
        "edges": [
            {
                "fact": "private fact",
                "source_episode_sequence": episode.source_sequence,
            }
            for episode in episodes
        ],
        "episodes": [
            {
                "source_sequence": episode.source_sequence,
                "source_hash": episode.source_hash,
                "session_id": episode.session_id,
            }
            for episode in episodes
        ],
        "canonical_graph_hash": "c" * 64,
    }


def _valid_retrieval(episodes: list[Episode]) -> dict:
    ids = [episode.session_id for episode in episodes]
    return {
        "question_id": "history-a",
        "query": "private retrieval query",
        "top_k": 10,
        "gold_episode_ids": ids[:1],
        "retrieved_episode_ids": ids,
        "results": [{"fact": "private retrieved fact"}],
        "metrics": {"evidence_recall_at_10": 1.0},
    }


class SemanticEvidenceCollectorTests(TestCase):
    def test_collector_whitelists_and_validates_safe_fields(self):
        collector = phase.H0SemanticEvidenceCollector()
        collector(
            {
                **_semantic_record(0),
                "repeated_trial_index": 0,
            }
        )

        self.assertEqual(set(collector.records[0]), phase.SAFE_SEMANTIC_FIELDS)
        self.assertNotIn("repeated_trial_index", collector.records[0])

        for forbidden in ("raw_prompt", "raw_response", "api_key", "entity_names"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ValueError):
                    collector({**_semantic_record(1), forbidden: "must not persist"})

        with self.assertRaises((TypeError, ValueError)):
            collector({"call_key": "incomplete"})
        missing_index = _semantic_record(2)
        missing_index.pop("repeated_trial_index")
        with self.assertRaises((TypeError, ValueError)):
            collector(missing_index)


class H0APhaseRunnerTests(IsolatedAsyncioTestCase):
    def test_reconstructs_frozen_source_zero_through_pinned_prompt_api(self):
        prepared = phase.prepare_h0_a_call(_frozen_primary_record())

        self.assertEqual(prepared.question_id, "07741c45")
        self.assertEqual(prepared.source_sequence, 0)
        self.assertEqual(prepared.previous_episode_count, 0)
        self.assertEqual(
            prepared.safe_evidence["episode_source_sha256"],
            "be983c489b10deea9c4d860f1e3203e4fa5d964154e004b814b2b5fee410156a",
        )
        self.assertEqual(
            prepared.safe_evidence["episode_body_sha256"],
            "830efb8271297fa54fd9a2f7f303b2c4dcade4ced4fbf5a84a3b8c90d940cf9f",
        )
        self.assertEqual(
            prepared.safe_evidence["message_evidence"],
            [
                {
                    "role": "system",
                    "character_count": 164,
                    "utf8_byte_count": 164,
                    "content_sha256": "da974483b3b54c8388e1ff5ef78b344599d191d56a36b13b687ef570c173ef3e",
                },
                {
                    "role": "user",
                    "character_count": 15757,
                    "utf8_byte_count": 15777,
                    "content_sha256": "0f1dfa08c1ae939b21bbad636d3be3004905804200aaa41e30c5741f5a6d3f77",
                },
            ],
        )
        self.assertEqual(
            prepared.safe_evidence["response_schema_sha256"],
            "c2681b52e56c961c526bfdbc1ff8bdc3503cd9a635372de896c998bf83e2c35b",
        )
        self.assertNotIn("messages", prepared.safe_evidence)
        self.assertNotIn("episode_body", prepared.safe_evidence)

    async def test_three_trials_use_fresh_clients_and_one_public_call_each(self):
        collector = phase.H0SemanticEvidenceCollector()
        ledger = H0AttemptLedger(stage_attempt_id="h0-a-offline-test")
        clients = []
        factory_indices = []

        class Client:
            def __init__(self, index: int, shared_ledger: H0AttemptLedger):
                self.index = index
                self.calls = []
                self.h0_ledger = shared_ledger
                self.client = _Closable()
                self.h0_token_counter = _Closable()

            @property
            def driver(self):
                raise AssertionError("H0-A must not access a database")

            @property
            def embedder(self):
                raise AssertionError("H0-A must not access embeddings")

            async def generate_response(self, messages, **kwargs):
                self.calls.append((messages, kwargs))
                _record_qualified_trial(
                    self.h0_ledger,
                    call_key="07741c45:0:extract_nodes.extract_message",
                    repeated_trial_index=self.index,
                )
                collector(
                    {
                        **_semantic_record(
                            self.index,
                            call_key="07741c45:0:extract_nodes.extract_message",
                        ),
                        "repeated_trial_index": self.index,
                    }
                )
                return {
                    "extracted_entities": [
                        {"name": f"private entity {self.index}", "episode_indices": [0]}
                    ]
                }

        def factory(index: int, supplied_ledger, supplied_collector):
            self.assertIs(supplied_ledger, ledger)
            self.assertIs(supplied_collector, collector)
            factory_indices.append(index)
            client = Client(index, supplied_ledger)
            clients.append(client)
            return client

        checkpoints = []
        result = await phase.run_h0_a(
            record=_frozen_primary_record(),
            stage_attempt_id="h0-a-offline-test",
            client_factory=factory,
            ledger=ledger,
            semantic_collector=collector,
            semantic_guardrail={},
            trial_checkpoint=checkpoints.append,
        )

        self.assertEqual(factory_indices, [0, 1, 2])
        self.assertEqual(len({id(client) for client in clients}), 3)
        self.assertEqual([len(client.calls) for client in clients], [1, 1, 1])
        self.assertTrue(all(client.client.closed for client in clients))
        self.assertTrue(all(client.h0_token_counter.closed for client in clients))
        self.assertEqual(len(ledger.trials), 3)
        self.assertEqual(len(ledger.attempts), 3)
        for client in clients:
            _messages, kwargs = client.calls[0]
            self.assertIs(kwargs["response_model"], prepared_model := phase.ExtractedEntities)
            self.assertEqual(kwargs["group_id"], "07741c45")
            self.assertEqual(kwargs["prompt_name"], "extract_nodes.extract_message")
            self.assertFalse(kwargs["attribute_extraction"])
            self.assertIsNotNone(prepared_model)
        self.assertEqual([item["repeated_trial_index"] for item in checkpoints], [0, 1, 2])
        self.assertTrue(checkpoints[-1]["final_stage_checks_passed"])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["logical_call_count"], 3)

        encoded = json.dumps(result, sort_keys=True)
        for forbidden in (
            "private entity",
            "private-session",
            "raw_prompt",
            "raw_response",
            "episode_body",
            "authorization",
            "api_key",
        ):
            self.assertNotIn(forbidden, encoded.lower())

    async def test_semantic_stage_runs_before_final_trial_checkpoint(self):
        collector = phase.H0SemanticEvidenceCollector()
        ledger = H0AttemptLedger(stage_attempt_id="h0-a-order-test")
        events = []

        class Client:
            def __init__(self, index, supplied_ledger, _collector):
                self.index = index
                self.h0_ledger = supplied_ledger
                self.client = _Closable()
                self.h0_token_counter = _Closable()

            async def generate_response(self, _messages, **_kwargs):
                _record_qualified_trial(
                    self.h0_ledger,
                    call_key="07741c45:0:extract_nodes.extract_message",
                    repeated_trial_index=self.index,
                )
                collector(
                    {
                        **_semantic_record(
                            self.index,
                            call_key="07741c45:0:extract_nodes.extract_message",
                        ),
                        "repeated_trial_index": self.index,
                    }
                )
                return {"extracted_entities": []}

        def validate(_guardrail, records):
            events.append(("semantic", len(records)))
            return {"qualified": True, "observed_call_count": 1}

        with patch.object(phase, "validate_semantic_stage", side_effect=validate):
            await phase.run_h0_a(
                record=_frozen_primary_record(),
                stage_attempt_id="h0-a-order-test",
                client_factory=Client,
                ledger=ledger,
                semantic_collector=collector,
                semantic_guardrail={},
                trial_checkpoint=lambda event: events.append(
                    ("checkpoint", event["repeated_trial_index"])
                ),
            )

        self.assertEqual(
            events,
            [
                ("checkpoint", 0),
                ("checkpoint", 1),
                ("semantic", 3),
                ("checkpoint", 2),
            ],
        )

    async def test_rejects_client_with_a_different_stage_ledger(self):
        expected = H0AttemptLedger(stage_attempt_id="h0-a-ledger-test")
        other = H0AttemptLedger(stage_attempt_id="h0-a-other-ledger")
        collector = phase.H0SemanticEvidenceCollector()

        class Client:
            h0_ledger = other
            client = _Closable()
            h0_token_counter = _Closable()

            async def generate_response(self, *_args, **_kwargs):
                raise AssertionError("mismatched ledger must fail before generation")

        with self.assertRaisesRegex(H0ManifestError, "shared stage ledger"):
            await phase.run_h0_a(
                record=_frozen_primary_record(),
                stage_attempt_id="h0-a-ledger-test",
                client_factory=lambda *_args: Client(),
                ledger=expected,
                semantic_collector=collector,
                semantic_guardrail={},
                trial_checkpoint=lambda _event: None,
            )
        self.assertTrue(Client.client.closed)
        self.assertTrue(Client.h0_token_counter.closed)


class FullHistoryEvidenceTests(TestCase):
    def test_valid_outputs_are_reduced_to_safe_counts_hashes_and_flags(self):
        episodes = [_episode(0), _episode(1)]
        evidence = phase.validate_full_history_outputs(
            instance={
                "question_id": "history-a",
                "question": "private retrieval query",
                "answer_session_ids": [episodes[0].session_id],
            },
            episodes=episodes,
            graph_output=_valid_graph(episodes),
            retrieval_output=_valid_retrieval(episodes),
        )

        self.assertTrue(evidence["qualified"])
        self.assertTrue(evidence["episode_mapping_exact"])
        self.assertTrue(evidence["edge_attribution_complete"])
        self.assertEqual(evidence["entity_count"], 1)
        self.assertEqual(evidence["top_k"], 10)
        encoded = json.dumps(evidence, sort_keys=True)
        for forbidden in (
            "private entity",
            "private fact",
            "private retrieval query",
            "private-session",
            '"entities"',
            '"edges"',
            '"results"',
            '"query"',
        ):
            self.assertNotIn(forbidden, encoded)

    def test_mapping_and_edge_attribution_failures_are_collected(self):
        episodes = [_episode(0), _episode(1)]
        graph = _valid_graph(episodes)
        graph["episodes"] = [
            graph["episodes"][0],
            {"source_sequence": None, "source_hash": "unexpected", "session_id": None},
            {"source_sequence": 9, "source_hash": "f" * 64, "session_id": "extra"},
        ]
        graph["edges"] = [
            {"source_episode_sequence": None},
            {"source_episode_sequence": [0, 9]},
        ]

        evidence = phase.validate_full_history_outputs(
            instance={"question_id": "history-a", "answer_session_ids": ["gold"]},
            episodes=episodes,
            graph_output=graph,
            retrieval_output=_valid_retrieval(episodes),
        )

        self.assertFalse(evidence["qualified"])
        self.assertGreaterEqual(evidence["unknown_mapping_count"], 2)
        self.assertEqual(
            set(evidence["failure_codes"]),
            {
                "episode_mapping_count_mismatch",
                "episode_mapping_not_exact",
                "unknown_episode_mapping",
                "edge_attribution_missing",
                "edge_attribution_out_of_scope",
            },
        )

    def test_semantic_graph_and_retrieval_guards_fail_closed(self):
        episodes = [_episode(0)]
        graph = _valid_graph(episodes)
        graph["entities"] = []
        retrieval = _valid_retrieval(episodes)
        retrieval.update(
            {
                "top_k": 9,
                "gold_episode_ids": [],
                "metrics": {"evidence_recall_at_10": 0.0},
            }
        )

        evidence = phase.validate_full_history_outputs(
            instance={"question_id": "history-a", "answer_session_ids": []},
            episodes=episodes,
            graph_output=graph,
            retrieval_output=retrieval,
        )

        self.assertFalse(evidence["qualified"])
        self.assertEqual(
            set(evidence["failure_codes"]),
            {
                "semantic_graph_empty",
                "gold_evidence_empty",
                "retrieval_top_k_not_10",
                "evidence_recall_at_10_nonpositive",
            },
        )


class FullHistoryPhaseRunnerTests(IsolatedAsyncioTestCase):
    async def test_episode_scope_uses_question_id_for_implicit_graphiti_calls(self):
        question_id = "07741c45"
        episodes = [
            _history_episode(question_id, 0),
            _history_episode(question_id, 1),
        ]
        collector = phase.H0SemanticEvidenceCollector()
        ledger = H0AttemptLedger(stage_attempt_id="h0-q1-b-replacement-003")
        observed_scopes = []

        async def ingest(_graph, episode):
            observed_scopes.append(current_episode_key())
            call_key = (
                f"{question_id}:{episode.source_sequence}:dedupe_nodes.nodes"
            )
            _record_qualified_trial(
                ledger,
                call_key=call_key,
                repeated_trial_index=0,
            )
            collector(
                _semantic_record(
                    episode.source_sequence,
                    call_key=call_key,
                )
            )

        retrieval = _valid_retrieval(episodes)
        retrieval["question_id"] = question_id
        with patch.object(
            phase,
            "validate_semantic_stage",
            return_value={"qualified": True, "observed_call_count": 2},
        ):
            result = await phase.run_full_history(
                instance={
                    "question_id": question_id,
                    "answer_session_ids": [episodes[0].session_id],
                },
                episodes=episodes,
                stage_attempt_id="h0-q1-b-replacement-003",
                graph_factory=lambda: _graph_with_ledger(ledger),
                clear_graph=lambda _graph: None,
                assert_graph_empty=lambda _graph: None,
                close_graph=lambda _graph: None,
                ingest_episode=ingest,
                export_graph=lambda _graph, supplied, _group_id: _valid_graph(supplied),
                evaluate_retrieval=lambda _graph, _instance, _episodes: retrieval,
                source_checkpoint=lambda _event: None,
                semantic_collector=collector,
                semantic_guardrail={},
                ledger=ledger,
            )

        self.assertTrue(result["qualified"])
        self.assertEqual(
            observed_scopes,
            [(question_id, 0), (question_id, 1)],
        )

    async def test_lifecycle_is_fresh_sequential_and_final_checkpoint_is_last(self):
        episodes = [_episode(2), _episode(0), _episode(1)]
        ordered = sorted(episodes, key=lambda episode: episode.source_sequence)
        collector = phase.H0SemanticEvidenceCollector()
        ledger = H0AttemptLedger(stage_attempt_id="h0-b-offline-test")
        events = []
        graph = _graph_with_ledger(ledger)

        async def graph_factory():
            events.append("factory")
            return graph

        async def clear_graph(actual):
            self.assertIs(actual, graph)
            events.append("clear")

        async def assert_graph_empty(actual):
            self.assertIs(actual, graph)
            events.append("assert-empty")

        async def ingest(actual, episode):
            self.assertIs(actual, graph)
            events.append(f"ingest-{episode.source_sequence}")
            for ordinal in range(2):
                call_key = f"history-a:{episode.source_sequence}:prompt-{ordinal}"
                _record_qualified_trial(
                    ledger,
                    call_key=call_key,
                    repeated_trial_index=0,
                )
                collector(
                    _semantic_record(
                        episode.source_sequence * 2 + ordinal,
                        call_key=call_key,
                    )
                )

        async def export(actual, supplied, group_id):
            self.assertIs(actual, graph)
            self.assertEqual(supplied, ordered)
            self.assertEqual(group_id, "history-a")
            events.append("export")
            return _valid_graph(ordered)

        async def retrieve(actual, instance, supplied):
            self.assertIs(actual, graph)
            self.assertEqual(instance["question_id"], "history-a")
            self.assertEqual(supplied, ordered)
            events.append("retrieval")
            return _valid_retrieval(ordered)

        async def checkpoint(event):
            events.append(f"checkpoint-{event['source_sequence']}")

        async def close(actual):
            self.assertIs(actual, graph)
            events.append("close")

        def validate(_guardrail, records):
            events.append("semantic")
            self.assertEqual(len(records), 6)
            return {"qualified": True, "observed_call_count": 6}

        with patch.object(phase, "validate_semantic_stage", side_effect=validate):
            result = await phase.run_full_history(
                instance={
                    "question_id": "history-a",
                    "question": "private query",
                    "answer_session_ids": [ordered[0].session_id],
                },
                episodes=episodes,
                stage_attempt_id="h0-b-offline-test",
                graph_factory=graph_factory,
                clear_graph=clear_graph,
                assert_graph_empty=assert_graph_empty,
                close_graph=close,
                ingest_episode=ingest,
                export_graph=export,
                evaluate_retrieval=retrieve,
                source_checkpoint=checkpoint,
                semantic_collector=collector,
                semantic_guardrail={},
                ledger=ledger,
            )

        self.assertTrue(result["qualified"])
        self.assertEqual(result["logical_call_count"], 6)
        self.assertEqual(result["http_attempt_count"], 6)
        self.assertEqual(result["retry_count"], 0)
        self.assertEqual(
            events,
            [
                "factory",
                "clear",
                "assert-empty",
                "ingest-0",
                "checkpoint-0",
                "ingest-1",
                "checkpoint-1",
                "ingest-2",
                "export",
                "retrieval",
                "semantic",
                "close",
                "checkpoint-2",
            ],
        )

    async def test_failed_final_validation_withholds_final_checkpoint(self):
        episodes = [_episode(0), _episode(1)]
        collector = phase.H0SemanticEvidenceCollector()
        ledger = H0AttemptLedger(stage_attempt_id="h0-b-failed-validation")
        checkpoints = []
        closed = []

        async def ingest(_graph, episode):
            call_key = f"history-a:{episode.source_sequence}:extract"
            _record_qualified_trial(
                ledger,
                call_key=call_key,
                repeated_trial_index=0,
            )
            collector(_semantic_record(episode.source_sequence, call_key=call_key))

        graph_output = _valid_graph(episodes)
        graph_output["entities"] = []
        result = await phase.run_full_history(
            instance={"question_id": "history-a", "answer_session_ids": ["gold"]},
            episodes=episodes,
            stage_attempt_id="h0-b-failed-validation",
            graph_factory=lambda: _graph_with_ledger(ledger),
            clear_graph=lambda _graph: None,
            assert_graph_empty=lambda _graph: None,
            close_graph=lambda _graph: closed.append(True),
            ingest_episode=ingest,
            export_graph=lambda _graph, _episodes, _group_id: graph_output,
            evaluate_retrieval=lambda _graph, _instance, _episodes: _valid_retrieval(
                episodes
            ),
            source_checkpoint=checkpoints.append,
            semantic_collector=collector,
            semantic_guardrail={},
            ledger=ledger,
        )

        self.assertFalse(result["qualified"])
        self.assertIn("semantic_graph_empty", result["failure_codes"])
        self.assertEqual([item["source_sequence"] for item in checkpoints], [0])
        self.assertEqual(closed, [True])


class H0BCOrchestrationTests(IsolatedAsyncioTestCase):
    def test_exact_b_and_c_history_workloads_are_frozen(self):
        corpus = _calibration_corpus()
        h0_b = phase.build_h0_full_history_workload(corpus, "H0-B")
        h0_c = phase.build_h0_full_history_workload(corpus, "H0-C")

        self.assertEqual([(item.question_id, len(item.episodes)) for item in h0_b], [
            ("07741c45", 49)
        ])
        self.assertEqual(
            [(item.question_id, len(item.episodes)) for item in h0_c],
            [("b6019101", 49), ("6071bd76", 46), ("a2f3aa27", 44)],
        )
        self.assertEqual(sum(len(item.episodes) for item in h0_c), 139)

    async def test_h0_c_infrastructure_recovery_restarts_all_three_histories(self):
        corpus = _calibration_corpus()
        first_calls = []

        async def interrupted_runner(*, item, stage_attempt_id, phase_name):
            first_calls.append((stage_attempt_id, item.question_id, phase_name))
            if item.question_id == "6071bd76":
                raise H0InfrastructureError("vllm_unreachable: stop_and_report")
            semantic = _semantic_record(
                0,
                call_key=f"{item.question_id}:0:extract_nodes.extract_message",
            )
            semantic.pop("repeated_trial_index")
            return {
                "qualified": True,
                "question_id": item.question_id,
                "semantic_records": [semantic],
            }

        with self.assertRaisesRegex(H0InfrastructureError, "vllm_unreachable"):
            await phase.run_h0_full_history_phase(
                corpus=corpus,
                phase_name="H0-C",
                stage_attempt_id="h0-c-attempt-001",
                history_runner=interrupted_runner,
                semantic_guardrail={},
            )
        self.assertEqual(
            [question_id for _attempt, question_id, _phase in first_calls],
            ["b6019101", "6071bd76"],
        )

        with self.assertRaisesRegex(H0ManifestError, "new attempt"):
            await phase.run_h0_full_history_phase(
                corpus=corpus,
                phase_name="H0-C",
                stage_attempt_id="h0-c-attempt-001",
                prior_interrupted_attempt_id="h0-c-attempt-001",
                history_runner=lambda **_kwargs: {"qualified": True},
                semantic_guardrail={},
            )

        rerun_calls = []

        async def recovered_runner(*, item, stage_attempt_id, phase_name):
            rerun_calls.append((stage_attempt_id, item.question_id, phase_name))
            semantic = _semantic_record(
                len(rerun_calls),
                call_key=f"{item.question_id}:0:extract_nodes.extract_message",
            )
            semantic.pop("repeated_trial_index")
            return {
                "qualified": True,
                "question_id": item.question_id,
                "semantic_records": [semantic],
            }

        result = await phase.run_h0_full_history_phase(
            corpus=corpus,
            phase_name="H0-C",
            stage_attempt_id="h0-c-attempt-002",
            prior_interrupted_attempt_id="h0-c-attempt-001",
            history_runner=recovered_runner,
            semantic_guardrail={},
        )
        self.assertEqual(
            [question_id for _attempt, question_id, _phase in rerun_calls],
            ["b6019101", "6071bd76", "a2f3aa27"],
        )
        self.assertTrue(result["qualified"])
        self.assertEqual(result["completed_history_count"], 3)
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn("private-", encoded)

    async def test_h0_c_rejects_constant_payload_across_distinct_histories(self):
        corpus = _calibration_corpus()
        source_zero_calls = [
            f"{question_id}:0:extract_nodes.extract_message"
            for question_id in ("b6019101", "6071bd76", "a2f3aa27")
        ]
        guardrail = {"cross_call_constant_detection_groups": [source_zero_calls]}

        async def constant_runner(*, item, **_kwargs):
            return {
                "qualified": True,
                "question_id": item.question_id,
                "semantic_records": [
                    {key: value for key, value in {
                        **_semantic_record(
                            0,
                            call_key=(
                                f"{item.question_id}:0:extract_nodes.extract_message"
                            ),
                        ),
                        "semantic_payload_sha256": "a" * 64,
                    }.items() if key != "repeated_trial_index"}
                ],
            }

        result = await phase.run_h0_full_history_phase(
            corpus=corpus,
            phase_name="H0-C",
            stage_attempt_id="h0-c-constant-semantic",
            history_runner=constant_runner,
            semantic_guardrail=guardrail,
        )

        self.assertFalse(result["qualified"])
        self.assertEqual(
            result["failure_codes"], ["cross_history_semantic_stage_failure"]
        )
        self.assertEqual(result["completed_history_count"], 3)
        self.assertFalse(result["partial_qualification_reusable"])
        self.assertNotIn("semantic_records", result)

    async def test_semantic_stage_failure_withholds_final_checkpoint(self):
        episodes = [_episode(0)]
        collector = phase.H0SemanticEvidenceCollector()
        ledger = H0AttemptLedger(stage_attempt_id="h0-b-semantic-failure")
        checkpoints = []
        closed = []

        async def ingest(_graph, episode):
            call_key = f"history-a:{episode.source_sequence}:extract"
            _record_qualified_trial(
                ledger,
                call_key=call_key,
                repeated_trial_index=0,
            )
            collector(_semantic_record(episode.source_sequence, call_key=call_key))

        with patch.object(
            phase,
            "validate_semantic_stage",
            side_effect=H0SemanticError("private semantic diagnostic"),
        ):
            result = await phase.run_full_history(
                instance={"question_id": "history-a", "answer_session_ids": ["gold"]},
                episodes=episodes,
                stage_attempt_id="h0-b-semantic-failure",
                graph_factory=lambda: _graph_with_ledger(ledger),
                clear_graph=lambda _graph: None,
                assert_graph_empty=lambda _graph: None,
                close_graph=lambda _graph: closed.append(True),
                ingest_episode=ingest,
                export_graph=lambda _graph, _episodes, _group_id: _valid_graph(episodes),
                evaluate_retrieval=lambda _graph, _instance, _episodes: _valid_retrieval(
                    episodes
                ),
                source_checkpoint=checkpoints.append,
                semantic_collector=collector,
                semantic_guardrail={},
                ledger=ledger,
            )

        self.assertFalse(result["qualified"])
        self.assertEqual(result["failure_codes"], ["semantic_stage_failure"])
        self.assertEqual(checkpoints, [])
        self.assertEqual(closed, [True])
        self.assertNotIn("private semantic diagnostic", json.dumps(result))

    async def test_cleanup_runs_when_ingestion_or_validation_raises(self):
        episodes = [_episode(0), _episode(1)]

        for failure_point in ("ingest", "export"):
            with self.subTest(failure_point=failure_point):
                closed = []
                collector = phase.H0SemanticEvidenceCollector()
                ledger = H0AttemptLedger(
                    stage_attempt_id=f"h0-b-{failure_point}-failure"
                )

                async def ingest(_graph, episode):
                    if failure_point == "ingest" and episode.source_sequence == 1:
                        raise RuntimeError("ingestion failed")
                    call_key = f"history-a:{episode.source_sequence}:extract"
                    _record_qualified_trial(
                        ledger,
                        call_key=call_key,
                        repeated_trial_index=0,
                    )
                    collector(_semantic_record(episode.source_sequence, call_key=call_key))

                async def export(_graph, _episodes, _group_id):
                    if failure_point == "export":
                        raise RuntimeError("export failed")
                    return _valid_graph(episodes)

                with self.assertRaises(RuntimeError):
                    await phase.run_full_history(
                        instance={
                            "question_id": "history-a",
                            "answer_session_ids": ["gold"],
                        },
                        episodes=episodes,
                        stage_attempt_id=f"h0-b-{failure_point}-failure",
                        graph_factory=lambda: _graph_with_ledger(ledger),
                        clear_graph=lambda _graph: None,
                        assert_graph_empty=lambda _graph: None,
                        close_graph=lambda _graph: closed.append(True),
                        ingest_episode=ingest,
                        export_graph=export,
                        evaluate_retrieval=lambda _graph, _instance, _episodes: (
                            _valid_retrieval(episodes)
                        ),
                        source_checkpoint=lambda _event: None,
                        semantic_collector=collector,
                        semantic_guardrail={},
                        ledger=ledger,
                    )

                self.assertEqual(closed, [True])

    async def test_cleanup_failure_never_replaces_primary_infrastructure_error(self):
        episodes = [_episode(0)]
        ledger = H0AttemptLedger(stage_attempt_id="h0-b-primary-infrastructure")
        primary = H0InfrastructureError("vllm_unreachable: private-primary-detail")
        close_attempts = []

        async def ingest(_graph, _episode):
            raise primary

        async def close(_graph):
            close_attempts.append(True)
            raise RuntimeError("private-cleanup-detail")

        with self.assertRaises(H0InfrastructureError) as raised:
            await phase.run_full_history(
                instance={"question_id": "history-a", "answer_session_ids": ["gold"]},
                episodes=episodes,
                stage_attempt_id="h0-b-primary-infrastructure",
                graph_factory=lambda: _graph_with_ledger(ledger),
                clear_graph=lambda _graph: None,
                assert_graph_empty=lambda _graph: None,
                close_graph=close,
                ingest_episode=ingest,
                export_graph=lambda _graph, _episodes, _group_id: _valid_graph(episodes),
                evaluate_retrieval=lambda _graph, _instance, _episodes: (
                    _valid_retrieval(episodes)
                ),
                source_checkpoint=lambda _event: None,
                semantic_collector=phase.H0SemanticEvidenceCollector(),
                semantic_guardrail={},
                ledger=ledger,
            )

        self.assertIs(raised.exception, primary)
        self.assertEqual(close_attempts, [True])

    async def test_secondary_cleanup_failure_is_sanitized_and_sink_failure_is_ignored(self):
        episodes = [_episode(0)]
        ledger = H0AttemptLedger(stage_attempt_id="h0-b-secondary-cleanup")
        primary = H0InfrastructureError("vllm_unreachable: private-primary-detail")
        cleanup_events = []

        async def ingest(_graph, _episode):
            raise primary

        async def close(_graph):
            raise RuntimeError("private-cleanup-detail")

        async def cleanup_error_sink(event):
            cleanup_events.append(event)
            raise RuntimeError("private-sink-detail")

        with self.assertRaises(H0InfrastructureError) as raised:
            await phase.run_full_history(
                instance={"question_id": "history-a", "answer_session_ids": ["gold"]},
                episodes=episodes,
                stage_attempt_id="h0-b-secondary-cleanup",
                graph_factory=lambda: _graph_with_ledger(ledger),
                clear_graph=lambda _graph: None,
                assert_graph_empty=lambda _graph: None,
                close_graph=close,
                ingest_episode=ingest,
                export_graph=lambda _graph, _episodes, _group_id: _valid_graph(episodes),
                evaluate_retrieval=lambda _graph, _instance, _episodes: (
                    _valid_retrieval(episodes)
                ),
                source_checkpoint=lambda _event: None,
                cleanup_error_sink=cleanup_error_sink,
                semantic_collector=phase.H0SemanticEvidenceCollector(),
                semantic_guardrail={},
                ledger=ledger,
            )

        self.assertIs(raised.exception, primary)
        self.assertEqual(len(cleanup_events), 1)
        event = cleanup_events[0]
        self.assertEqual(event["event"], "secondary_cleanup_failure")
        self.assertEqual(event["primary_failure_class"], "infrastructure")
        self.assertEqual(event["cleanup_failure_class"], "cleanup_error")
        self.assertFalse(event["raw_errors_persisted"])
        encoded = json.dumps(event, sort_keys=True)
        self.assertNotIn("private-primary-detail", encoded)
        self.assertNotIn("private-cleanup-detail", encoded)
        self.assertNotIn("private-sink-detail", encoded)

    async def test_source_without_any_llm_or_semantic_evidence_fails_closed(self):
        episodes = [_episode(0)]
        ledger = H0AttemptLedger(stage_attempt_id="h0-b-missing-call")
        closed = []

        with self.assertRaisesRegex(H0ManifestError, "source.*LLM|semantic"):
            await phase.run_full_history(
                instance={"question_id": "history-a", "answer_session_ids": ["gold"]},
                episodes=episodes,
                stage_attempt_id="h0-b-missing-call",
                graph_factory=lambda: _graph_with_ledger(ledger),
                clear_graph=lambda _graph: None,
                assert_graph_empty=lambda _graph: None,
                close_graph=lambda _graph: closed.append(True),
                ingest_episode=lambda _graph, _episode: None,
                export_graph=lambda _graph, _episodes, _group_id: _valid_graph(episodes),
                evaluate_retrieval=lambda _graph, _instance, _episodes: (
                    _valid_retrieval(episodes)
                ),
                source_checkpoint=lambda _event: None,
                semantic_collector=phase.H0SemanticEvidenceCollector(),
                semantic_guardrail={},
                ledger=ledger,
            )
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    import unittest

    unittest.main()
