"""Offline contracts for the C5 live boundary; no service is contacted."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import dataset  # noqa: E402
import native_characterization_c5 as c5  # noqa: E402
import native_characterization_c5_live_core as live  # noqa: E402
from neo4j.exceptions import ConstraintError, ServiceUnavailable, TransactionError  # noqa: E402


RUN_ID = "c5-0123456789abcdef"
HISTORY_ID = "07741c45"


def source_hashes() -> list[str]:
    return [
        hashlib.sha256(f"episode-{index}".encode("ascii")).hexdigest()
        for index in range(live.FROZEN_EPISODE_COUNT)
    ]


def episodes() -> list[c5.Episode]:
    values: list[c5.Episode] = []
    for index, source_hash in enumerate(source_hashes()):
        payload = dataset.Episode(
            question_id=HISTORY_ID,
            group_id=HISTORY_ID,
            session_id=f"session-{index:03d}",
            source_sequence=index,
            source_hash=source_hash,
            reference_time="2026-08-01T00:00:00+00:00",
            body=f"offline fixture {index}",
        )
        values.append(c5.Episode(source_sequence=index, payload=payload))
    return values


def frozen_schedule() -> dict[str, object]:
    freeze = json.loads(
        (ROOT / "artifacts/native_characterization/freeze_reference_aligned_64k.json")
        .read_text("ascii")
    )
    return live.load_frozen_e4_schedule(
        freeze,
        run_id=RUN_ID,
        episode_source_hashes=source_hashes(),
    )


def graph(namespace: str, *, name: str = "alpha") -> dict[str, object]:
    return {
        "entities": [
            {
                "group_id": namespace,
                "name": name,
                "labels": ["Entity"],
                "summary": "stable",
                "attributes": {},
            }
        ],
        "edges": [],
        "episodes": [
            {
                "source_sequence": index,
                "source_hash": source_hash,
                "session_id": f"session-{index:03d}",
            }
            for index, source_hash in enumerate(source_hashes())
        ],
    }


def retrieval(*ids: str) -> dict[str, object]:
    return {
        "question_id": HISTORY_ID,
        "retrieved_episode_ids": list(ids),
        "gold_episode_ids": ["session-001"],
        "metrics": {
            "evidence_recall_at_5": 1.0,
            "evidence_recall_at_10": 1.0,
            "episode_set_overlap_with_m0": 1.0,
            "rank_biased_overlap_with_m0": 1.0,
        },
    }


class FakeStore:
    """An awaitable durability boundary that records acknowledged write order."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.next_event_sequence = 0
        self.intents: list[dict[str, object]] = []
        self.publications: list[dict[str, object]] = []
        self.failures: list[dict[str, object]] = []
        self.episode_checkpoints: list[dict[str, object]] = []
        self.block_checkpoints: list[dict[str, object]] = []
        self.root_checkpoints: list[dict[str, object]] = []
        self.direct_observations: list[dict[str, object]] = []
        self.finalized: list[list[dict[str, object]]] = []

    async def append_intent_event(self, value: dict[str, object]) -> dict[str, object]:
        event = self._event(value, "i")
        self.intents.append(event)
        self.calls.append(("intent", value["block_index"], value["source_sequence"]))
        return event

    async def append_publication_event(self, value: dict[str, object]) -> dict[str, object]:
        event = self._event(value, "p")
        self.publications.append(event)
        self.calls.append(("publication", value["block_index"], value["source_sequence"]))
        return event

    async def append_failure_event(self, value: dict[str, object]) -> dict[str, object]:
        event = self._event(value, "f")
        self.failures.append(event)
        self.calls.append(("failure", value["block_index"], value["source_sequence"]))
        return event

    async def write_episode_checkpoint(self, **value: object) -> None:
        self.episode_checkpoints.append(dict(value))
        self.calls.append(("episode-checkpoint", value["block_index"], value["source_sequence"]))

    async def write_block_checkpoint(self, **value: object) -> None:
        self.block_checkpoints.append(dict(value))
        self.calls.append(("block-checkpoint", value["block_index"]))

    async def write_root_checkpoint(self, **value: object) -> None:
        self.root_checkpoints.append(dict(value))
        self.calls.append(("root-checkpoint", tuple(value["completed_block_indices"])))

    async def finalize_success(self, block_results: list[dict[str, object]]) -> None:
        self.finalized.append(deepcopy(block_results))
        self.calls.append(("finalize",))

    async def finalize_direct_observation(self, **value: object) -> dict[str, object]:
        observation = {
            "status": "direct_invariant_observed",
            "overall_interpretation": c5.DIRECT_INVARIANT_VIOLATION_OBSERVED,
            "failure_event_payload_sha256": value["failure_event"]["payload_sha256"],
            "completed_block_indices": list(value["completed_block_indices"]),
        }
        self.direct_observations.append(deepcopy(observation))
        self.calls.append(("direct-observation",))
        return observation

    def _event(self, value: dict[str, object], prefix: str) -> dict[str, object]:
        event = {
            **value,
            "event_sequence": self.next_event_sequence,
            "payload_sha256": prefix * 64,
        }
        self.next_event_sequence += 1
        return event


class FakeRuntime:
    def __init__(
        self,
        block: live.C5Block,
        store: FakeStore,
        *,
        counts: live.NamespaceCounts = live.NamespaceCounts(0, 0),
        graph_name: str = "alpha",
        retrieved_ids: tuple[str, ...] = ("session-001", "session-002"),
        fail_at: int | None = None,
        synchronize_first_wave: bool = False,
    ) -> None:
        self.block = block
        self.store = store
        self.counts = counts
        self.graph_name = graph_name
        self.retrieved_ids = retrieved_ids
        self.fail_at = fail_at
        self.synchronize_first_wave = synchronize_first_wave
        self.release = asyncio.Event()
        self.active = 0
        self.max_active = 0
        self.added: list[int] = []
        self.clear_count = 0
        self.closed = False

    async def namespace_counts(self) -> live.NamespaceCounts:
        return self.counts

    async def clear_namespace(self) -> None:
        self.clear_count += 1
        self.counts = live.NamespaceCounts(0, 0)

    async def add_episode(self, episode: c5.Episode) -> dict[str, object]:
        source = episode.source_sequence
        self.store.calls.append(("add-start", self.block.block_index, source))
        self.added.append(source)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.synchronize_first_wave:
            if self.active == self.block.concurrency:
                self.release.set()
            await self.release.wait()
        else:
            await asyncio.sleep(0)
        try:
            if source == self.fail_at:
                raise RuntimeError("secret infrastructure response")
            return {"work_counts": {"add_episode_calls": 1}}
        finally:
            self.active -= 1

    async def export_canonical_graph(self) -> dict[str, object]:
        return graph(self.block.graph_namespace, name=self.graph_name)

    async def evaluate_retrieval(
        self, reference_episode_ids: list[str] | None
    ) -> dict[str, object]:
        return retrieval(*self.retrieved_ids)

    async def close(self) -> None:
        self.closed = True


class FrozenScheduleTests(TestCase):
    def test_loader_uses_exact_64k_freeze_history_grid_and_namespaces(self) -> None:
        schedule = frozen_schedule()

        self.assertEqual(schedule["history_id"], HISTORY_ID)
        self.assertEqual(schedule["episode_ids"], [f"{HISTORY_ID}:{i}" for i in range(49)])
        self.assertEqual(schedule["concurrency_grid"], [1, 2, 4, 8])
        self.assertEqual(
            [item["graph_namespace"] for item in schedule["block_schedules"]],
            [
                "nc-e4-1434fcb947df5c3d",
                "nc-e4-b352061ffa0d4b21",
                "nc-e4-c15538d1fe2801cb",
                "nc-e4-2a427029b1a8b2ac",
            ],
        )
        self.assertTrue(
            all(item["absolute_arrival_offsets_ns"] == [0] * 49 for item in schedule["block_schedules"])
        )

    def test_failure_classification_separates_infrastructure_from_direct_evidence(self) -> None:
        infrastructure = live.classify_live_failure(RuntimeError("connection lost"))
        direct = live.classify_live_failure(c5.TransactionFailure("transaction aborted"))

        self.assertEqual(infrastructure.failure_kind, live.INFRASTRUCTURE_FAILURE)
        self.assertIsNone(infrastructure.scientific_interpretation)
        self.assertEqual(direct.failure_kind, live.DIRECT_INVARIANT_FAILURE)
        self.assertEqual(direct.scientific_interpretation, c5.DIRECT_INVARIANT_VIOLATION_OBSERVED)

    def test_only_neo4j_constraint_errors_are_mapped_to_direct_transaction_evidence(self) -> None:
        constraint = live.classify_live_failure(ConstraintError("private conflict"))
        transaction = live.classify_live_failure(TransactionError("private driver state"))
        disconnected = live.classify_live_failure(ServiceUnavailable("private host"))

        self.assertEqual(constraint.failure_kind, live.DIRECT_INVARIANT_FAILURE)
        self.assertEqual(
            constraint.scientific_interpretation,
            c5.DIRECT_INVARIANT_VIOLATION_OBSERVED,
        )
        self.assertEqual(transaction.failure_kind, live.INFRASTRUCTURE_FAILURE)
        self.assertIsNone(transaction.scientific_interpretation)
        self.assertEqual(disconnected.failure_kind, live.INFRASTRUCTURE_FAILURE)
        self.assertIsNone(disconnected.scientific_interpretation)

    def test_qa_view_is_supplemental_and_contains_no_raw_output(self) -> None:
        view = live.build_supplemental_qa_view(
            {
                "qa_surface": "retrieved_evidence_answerability",
                "status": "SUCCESS",
                "correct": False,
                "accuracy": 0.0,
                "judge_model": "qwen3-32b-fp8",
                "judge_config_sha256": "a" * 64,
                "retrieval_payload_sha256": "b" * 64,
                "reader_generation_performed": False,
                "headline_interpretation_effect": "none",
                "raw_output": "private answer",
            }
        )

        self.assertEqual(view["status"], "SUCCESS")
        self.assertEqual(view["accuracy"], 0.0)
        self.assertEqual(view["headline_interpretation_effect"], "none")
        self.assertEqual(view["qa_surface"], "retrieved_evidence_answerability")
        self.assertEqual(view["judge_config_sha256"], "a" * 64)
        self.assertEqual(view["retrieval_payload_sha256"], "b" * 64)
        self.assertFalse(view["reader_generation_performed"])
        self.assertNotIn("private answer", repr(view))


class C5LiveCoreTests(IsolatedAsyncioTestCase):
    async def test_runtime_init_and_namespace_failures_are_durable_stops(self) -> None:
        for failure_stage in ("runtime_init", "namespace_check"):
            with self.subTest(failure_stage=failure_stage):
                store = FakeStore()

                class NamespaceFailureRuntime(FakeRuntime):
                    async def namespace_counts(self) -> live.NamespaceCounts:
                        raise ConnectionError("private neo4j endpoint")

                async def runtime_factory(block: live.C5Block) -> FakeRuntime:
                    if failure_stage == "runtime_init":
                        raise ConnectionError("private runtime endpoint")
                    return NamespaceFailureRuntime(block, store)

                result = await live.run_c5_live_core(
                    schedule=frozen_schedule(),
                    episodes=episodes(),
                    episode_source_hashes=source_hashes(),
                    runtime_factory=runtime_factory,
                    store=store,
                    now_ns=live.MonotonicCounter(),
                )

                self.assertEqual(result["status"], live.INCOMPLETE_NON_MERGEABLE)
                self.assertEqual(result["failure_stage"], failure_stage)
                self.assertEqual(len(store.failures), 1)
                self.assertEqual(store.failures[0]["failure_stage"], failure_stage)
                self.assertEqual(
                    store.root_checkpoints[-1]["status"],
                    live.INCOMPLETE_NON_MERGEABLE,
                )

    async def test_post_ingestion_failures_are_durable_recoverable_stops(self) -> None:
        for failure_stage in ("export", "retrieval", "judge", "close"):
            with self.subTest(failure_stage=failure_stage):
                store = FakeStore()

                class FailingRuntime(FakeRuntime):
                    async def export_canonical_graph(self) -> dict[str, object]:
                        if failure_stage == "export":
                            raise ConnectionError("private export endpoint")
                        return await super().export_canonical_graph()

                    async def evaluate_retrieval(
                        self, reference_episode_ids: list[str] | None
                    ) -> dict[str, object]:
                        if failure_stage == "retrieval":
                            raise ConnectionError("private embedding endpoint")
                        return await super().evaluate_retrieval(reference_episode_ids)

                    async def close(self) -> None:
                        self.closed = True
                        if failure_stage == "close":
                            raise ConnectionError("private close endpoint")

                async def runtime_factory(block: live.C5Block) -> FailingRuntime:
                    return FailingRuntime(block, store)

                async def evaluator(
                    _runtime: FakeRuntime, _block: live.C5Block
                ) -> dict[str, object]:
                    if failure_stage == "judge":
                        raise ConnectionError("private judge endpoint")
                    return {"status": "SUCCESS", "correct": True}

                result = await live.run_c5_live_core(
                    schedule=frozen_schedule(),
                    episodes=episodes(),
                    episode_source_hashes=source_hashes(),
                    runtime_factory=runtime_factory,
                    store=store,
                    now_ns=live.MonotonicCounter(),
                    qa_evaluator=evaluator,
                )

                self.assertEqual(result["status"], live.INCOMPLETE_NON_MERGEABLE)
                self.assertEqual(result["failed_block_index"], 0)
                self.assertEqual(result["failure_stage"], failure_stage)
                self.assertEqual(len(store.failures), 1)
                self.assertEqual(store.failures[0]["failure_stage"], failure_stage)
                self.assertEqual(store.failures[0]["failure_kind"], live.INFRASTRUCTURE_FAILURE)
                self.assertNotIn("private", repr(store.failures))
                self.assertEqual(
                    store.root_checkpoints[-1]["status"],
                    live.INCOMPLETE_NON_MERGEABLE,
                )

    async def test_true_async_grid_is_work_conserving_and_each_intent_precedes_add(self) -> None:
        store = FakeStore()
        runtimes: list[FakeRuntime] = []

        async def runtime_factory(block: live.C5Block) -> FakeRuntime:
            runtime = FakeRuntime(block, store, synchronize_first_wave=True)
            runtimes.append(runtime)
            return runtime

        result = await live.run_c5_live_core(
            schedule=frozen_schedule(),
            episodes=episodes(),
            episode_source_hashes=source_hashes(),
            runtime_factory=runtime_factory,
            store=store,
            now_ns=live.MonotonicCounter(),
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual([runtime.max_active for runtime in runtimes], [1, 2, 4, 8])
        self.assertTrue(all(runtime.added == list(range(49)) for runtime in runtimes))
        self.assertTrue(all(runtime.closed for runtime in runtimes))
        self.assertEqual(len(store.intents), 4 * 49)
        self.assertEqual(len(store.publications), 4 * 49)
        self.assertEqual(len(store.episode_checkpoints), 4 * 49)
        self.assertEqual(len(store.block_checkpoints), 4)
        self.assertEqual(len(store.root_checkpoints), 4)
        for block_index in range(4):
            block_intents = [
                event for event in store.intents if event["block_index"] == block_index
            ]
            block_publications = [
                event
                for event in store.publications
                if event["block_index"] == block_index
            ]
            self.assertEqual(
                len({event["arrival_timestamp_ns"] for event in block_intents}), 1
            )
            self.assertEqual(
                {event["arrival_timestamp_ns"] for event in block_publications},
                {block_intents[0]["arrival_timestamp_ns"]},
            )
            if block_index == 0:
                self.assertGreater(
                    block_publications[-1]["service_start_timestamp_ns"],
                    block_publications[-1]["arrival_timestamp_ns"],
                )
            for source in range(49):
                intent = store.calls.index(("intent", block_index, source))
                add = store.calls.index(("add-start", block_index, source))
                publication = store.calls.index(("publication", block_index, source))
                checkpoint = store.calls.index(("episode-checkpoint", block_index, source))
                self.assertLess(intent, add)
                self.assertLess(publication, checkpoint)

    async def test_infrastructure_failure_is_durable_stop_not_scientific_direct(self) -> None:
        store = FakeStore()
        runtimes: list[FakeRuntime] = []

        async def runtime_factory(block: live.C5Block) -> FakeRuntime:
            runtime = FakeRuntime(block, store, fail_at=0)
            runtimes.append(runtime)
            return runtime

        result = await live.run_c5_live_core(
            schedule=frozen_schedule(),
            episodes=episodes(),
            episode_source_hashes=source_hashes(),
            runtime_factory=runtime_factory,
            store=store,
            now_ns=live.MonotonicCounter(),
        )

        self.assertEqual(result["status"], live.INCOMPLETE_NON_MERGEABLE)
        self.assertEqual(result["failure_kind"], live.INFRASTRUCTURE_FAILURE)
        self.assertIsNone(result["scientific_interpretation"])
        self.assertEqual(len(runtimes), 1)
        self.assertEqual(len(store.failures), 1)
        self.assertEqual(store.failures[0]["error_class"], "builtins.RuntimeError")
        self.assertNotIn("secret infrastructure response", repr(store.failures))
        self.assertEqual(store.block_checkpoints, [])
        self.assertEqual(store.root_checkpoints[-1]["status"], live.INCOMPLETE_NON_MERGEABLE)
        self.assertEqual(store.direct_observations, [])

    async def test_direct_transaction_failure_writes_scientific_terminal_observation(self) -> None:
        store = FakeStore()

        class ConstraintRuntime(FakeRuntime):
            async def add_episode(self, episode: c5.Episode) -> dict[str, object]:
                self.store.calls.append(
                    ("add-start", self.block.block_index, episode.source_sequence)
                )
                raise ConstraintError("private constraint detail")

        async def runtime_factory(block: live.C5Block) -> ConstraintRuntime:
            return ConstraintRuntime(block, store)

        result = await live.run_c5_live_core(
            schedule=frozen_schedule(),
            episodes=episodes(),
            episode_source_hashes=source_hashes(),
            runtime_factory=runtime_factory,
            store=store,
            now_ns=live.MonotonicCounter(),
        )

        self.assertEqual(result["status"], "direct_invariant_observed")
        self.assertEqual(result["failure_kind"], live.DIRECT_INVARIANT_FAILURE)
        self.assertEqual(
            result["scientific_interpretation"],
            c5.DIRECT_INVARIANT_VIOLATION_OBSERVED,
        )
        self.assertEqual(len(store.failures), 1)
        self.assertEqual(len(store.direct_observations), 1)
        self.assertEqual(
            store.direct_observations[0]["overall_interpretation"],
            c5.DIRECT_INVARIANT_VIOLATION_OBSERVED,
        )
        self.assertLess(
            store.calls.index(("root-checkpoint", ())),
            store.calls.index(("direct-observation",)),
        )
        self.assertNotIn("private constraint", repr(store.direct_observations))

    async def test_resume_keeps_completed_blocks_and_restarts_partial_block_from_zero(self) -> None:
        store = FakeStore()
        runtimes: list[FakeRuntime] = []
        completed_block_result = c5.analyze_c5_block(
            concurrency=1,
            expected_episode_ids=[f"{HISTORY_ID}:{index}" for index in range(49)],
            publication_records=[
                {
                    "event_sequence": source * 2 + 1,
                    "source_sequence": source,
                    "arrival_timestamp_ns": source * 10,
                    "service_start_timestamp_ns": source * 10 + 1,
                    "publish_timestamp_ns": source * 10 + 2,
                    "caller_return_timestamp_ns": source * 10 + 2,
                    "worker_id": 0,
                    "transaction_status": "committed",
                    "work_counts": {"add_episode_calls": 1},
                }
                for source in range(49)
            ],
            canonical_graph_parity={"status": "pass"},
            retrieval_parity={"status": "pass"},
            execution_path_evidence={
                "treatment_is_concurrency_only": True,
                "live_graph_outputs_fixed": True,
                "complete_add_episode_units": True,
                "work_conserving_dispatch": True,
            },
        )
        completed_block_result.pop("payload_sha256")
        completed_block_result.update(
            {
                "block_index": 0,
                "graph_namespace": "nc-e4-1434fcb947df5c3d",
                "supplemental_qa": {
                    "status": "SUCCESS",
                    "accuracy": 1.0,
                    "headline_interpretation_effect": "none",
                },
            }
        )
        completed_block_result = c5.seal_payload(completed_block_result)
        serial_reference = live.build_serial_reference(
            graph("nc-e4-1434fcb947df5c3d"),
            retrieval("session-001", "session-002"),
        )

        async def runtime_factory(block: live.C5Block) -> FakeRuntime:
            counts = live.NamespaceCounts(3, 2) if block.block_index == 1 else live.NamespaceCounts(0, 0)
            runtime = FakeRuntime(block, store, counts=counts)
            runtimes.append(runtime)
            return runtime

        result = await live.run_c5_live_core(
            schedule=frozen_schedule(),
            episodes=episodes(),
            episode_source_hashes=source_hashes(),
            runtime_factory=runtime_factory,
            store=store,
            now_ns=live.MonotonicCounter(),
            resume_prefix=live.C5ResumePrefix(
                completed_block_indices=(0,),
                partial_block_index=1,
                serial_reference=serial_reference,
                completed_block_results=(completed_block_result,),
            ),
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual([runtime.block.block_index for runtime in runtimes], [1, 2, 3])
        self.assertEqual(runtimes[0].clear_count, 1)
        self.assertEqual(runtimes[0].added[0], 0)
        self.assertEqual(runtimes[0].added, list(range(49)))
        self.assertEqual(result["completed_block_indices"], [0, 1, 2, 3])
        self.assertEqual(len(result["block_results"]), 4)
        self.assertEqual(len(store.finalized), 1)
        self.assertEqual(len(store.finalized[0]), 4)
        self.assertEqual(store.finalized[0][0], completed_block_result)

    async def test_resume_rejects_completed_prefix_without_persisted_block_results(self) -> None:
        store = FakeStore()

        async def runtime_factory(block: live.C5Block) -> FakeRuntime:
            return FakeRuntime(block, store)

        with self.assertRaisesRegex(
            live.C5LiveCoreError, "resume_completed_block_results_missing"
        ):
            await live.run_c5_live_core(
                schedule=frozen_schedule(),
                episodes=episodes(),
                episode_source_hashes=source_hashes(),
                runtime_factory=runtime_factory,
                store=store,
                now_ns=live.MonotonicCounter(),
                resume_prefix=live.C5ResumePrefix(
                    completed_block_indices=(0,),
                    partial_block_index=1,
                    serial_reference=live.build_serial_reference(
                        graph("nc-e4-1434fcb947df5c3d"),
                        retrieval("session-001", "session-002"),
                    ),
                ),
            )

    async def test_graph_and_retrieval_parity_drive_headline_but_qa_does_not(self) -> None:
        store = FakeStore()

        async def runtime_factory(block: live.C5Block) -> FakeRuntime:
            return FakeRuntime(
                block,
                store,
                graph_name="changed" if block.concurrency == 4 else "alpha",
                retrieved_ids=("session-009",) if block.concurrency == 8 else ("session-001", "session-002"),
            )

        async def qa_evaluator(_runtime: FakeRuntime, _block: live.C5Block) -> dict[str, object]:
            return {"status": "SUCCESS", "correct": False, "raw_output": "must not persist"}

        result = await live.run_c5_live_core(
            schedule=frozen_schedule(),
            episodes=episodes(),
            episode_source_hashes=source_hashes(),
            runtime_factory=runtime_factory,
            store=store,
            now_ns=live.MonotonicCounter(),
            qa_evaluator=qa_evaluator,
        )
        blocks = result["block_results"]

        self.assertEqual(blocks[0]["canonical_graph_parity"]["status"], "pass")
        self.assertFalse(
            blocks[0]["execution_path_evidence"]["live_graph_outputs_replay_fixed"]
        )
        self.assertEqual(blocks[1]["interpretation"], c5.NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED)
        self.assertEqual(blocks[2]["canonical_graph_parity"]["status"], "mismatch")
        self.assertEqual(blocks[2]["interpretation"], c5.OUTCOME_INSTABILITY_OR_CONFOUNDED)
        self.assertTrue(
            any(
                "unfixed model outputs" in item
                for item in blocks[2]["confounded_evidence"]
            )
        )
        self.assertEqual(blocks[3]["retrieval_parity"]["status"], "mismatch")
        self.assertEqual(blocks[3]["interpretation"], c5.OUTCOME_INSTABILITY_OR_CONFOUNDED)
        self.assertTrue(all(item["supplemental_qa"]["accuracy"] == 0.0 for item in blocks))
        self.assertTrue(
            all(item["supplemental_qa"]["headline_interpretation_effect"] == "none" for item in blocks)
        )
        self.assertEqual(blocks[0]["retrieval_metrics"]["reference_surface"], "c1")
        self.assertEqual(blocks[0]["retrieval_metrics"]["evidence_recall_at_10"], 1.0)
        self.assertEqual(blocks[0]["retrieval_metrics"]["top_10_set_overlap_vs_c1"], 1.0)
        self.assertEqual(blocks[3]["retrieval_metrics"]["top_10_set_overlap_vs_c1"], 1.0)
        reference = blocks[0]["serial_reference"]
        self.assertEqual(len(reference["canonical_graph_sha256"]), 64)
        self.assertEqual(reference["retrieved_episode_ids"], ["session-001", "session-002"])
        self.assertNotIn("entities", repr(reference))
        self.assertNotIn("edges", repr(reference))
        self.assertNotIn("must not persist", repr(blocks))

    async def test_graphiti_adapter_calls_complete_add_episode_with_block_namespace(self) -> None:
        calls: list[dict[str, object]] = []
        retrieval_calls = 0

        class Graphiti:
            async def add_episode(self, **kwargs: object) -> None:
                calls.append(dict(kwargs))

        async def exporter(_graphiti: object, _episodes: object, group_id: str) -> dict[str, object]:
            return graph(group_id)

        async def evaluator(
            _graphiti: object,
            _instance: object,
            _episodes: object,
            reference_episode_ids: list[str] | None,
            top_k: int,
        ) -> dict[str, object]:
            nonlocal retrieval_calls
            retrieval_calls += 1
            self.assertEqual(top_k, 10)
            return {
                **retrieval("session-001"),
                "results": [{"rank": 1, "fact": "answer evidence"}],
                "metrics": {"evidence_recall_at_10": 1.0},
            }

        block = live.C5Block(0, 1, "nc-e4-1434fcb947df5c3d")
        runtime = live.GraphitiBlockRuntime(
            graphiti=Graphiti(),
            block=block,
            episodes=[item.payload for item in episodes()],
            instance={"question_id": HISTORY_ID, "question": "frozen", "answer_session_ids": []},
            graph_exporter=exporter,
            retrieval_evaluator=evaluator,
        )
        await runtime.add_episode(episodes()[0])
        await runtime.export_canonical_graph()
        first_retrieval = await runtime.evaluate_retrieval(None)
        cached_retrieval = runtime.cached_retrieval_result()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["group_id"], block.graph_namespace)
        self.assertEqual(calls[0]["name"], f"{HISTORY_ID}::episode::0000")
        self.assertEqual(retrieval_calls, 1)
        self.assertEqual(cached_retrieval, first_retrieval)
        cached_retrieval["results"][0]["fact"] = "mutated"
        self.assertEqual(runtime.cached_retrieval_result()["results"][0]["fact"], "answer evidence")

    async def test_cached_retrieval_is_unavailable_before_the_single_search(self) -> None:
        block = live.C5Block(0, 1, "nc-e4-1434fcb947df5c3d")

        class Graphiti:
            pass

        async def exporter(*_args: object) -> dict[str, object]:
            return graph(block.graph_namespace)

        async def evaluator(*_args: object) -> dict[str, object]:
            return retrieval("session-001")

        runtime = live.GraphitiBlockRuntime(
            graphiti=Graphiti(),
            block=block,
            episodes=[item.payload for item in episodes()],
            instance={"question_id": HISTORY_ID},
            graph_exporter=exporter,
            retrieval_evaluator=evaluator,
        )

        with self.assertRaisesRegex(live.C5LiveCoreError, "retrieval_not_evaluated"):
            runtime.cached_retrieval_result()


if __name__ == "__main__":
    import unittest

    unittest.main()
