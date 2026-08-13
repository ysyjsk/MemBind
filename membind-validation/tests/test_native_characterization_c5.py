"""Focused offline contracts for the frozen C5/E4 characterization core."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_c5 as c5  # noqa: E402


class FakeClock:
    def __init__(self, initial_ns: int = 0) -> None:
        self.current_ns = initial_ns
        self.wakeups: list[int] = []

    def now_ns(self) -> int:
        return self.current_ns

    def sleep_until_ns(self, timestamp_ns: int) -> None:
        if timestamp_ns < self.current_ns:
            raise AssertionError("clock moved backwards")
        self.current_ns = timestamp_ns
        self.wakeups.append(timestamp_ns)


class FakeWholeUpdate:
    def __init__(
        self,
        durations_ns: dict[int, int],
        *,
        fail_at: int | None = None,
        transaction_fail_at: int | None = None,
    ) -> None:
        self.durations_ns = durations_ns
        self.fail_at = fail_at
        self.transaction_fail_at = transaction_fail_at
        self.calls: list[tuple[int, int]] = []

    def __call__(self, episode: c5.Episode, service_start_ns: int) -> int:
        self.calls.append((episode.source_sequence, service_start_ns))
        if episode.source_sequence == self.fail_at:
            raise RuntimeError("synthetic service failure with secret-token")
        if episode.source_sequence == self.transaction_fail_at:
            raise c5.TransactionFailure("synthetic transaction failure with secret-token")
        return self.durations_ns[episode.source_sequence]


class FakeDurableWriter:
    def __init__(self) -> None:
        self.publications: list[dict[str, object]] = []
        self.failures: list[dict[str, object]] = []

    def persist_publication(self, record: dict[str, object]) -> None:
        self.publications.append(dict(record))

    def persist_failure(self, checkpoint: dict[str, object]) -> None:
        self.failures.append(dict(checkpoint))


def episodes(count: int) -> list[c5.Episode]:
    return [c5.Episode(source_sequence=index, payload={"index": index}) for index in range(count)]


class NativeCharacterizationC5Tests(TestCase):
    maxDiff = None

    def test_frozen_concurrency_grid_and_schedule_blocks_are_exact(self) -> None:
        schedule = c5.build_c5_schedule(
            history_id="07741c45",
            episode_source_hashes=["a" * 64, "b" * 64, "c" * 64],
            interarrival_ns=100,
        )

        self.assertEqual(schedule["schema_version"], c5.SCHEDULE_SCHEMA)
        self.assertEqual(schedule["stage"], "C5/E4_OFFLINE_SCHEDULE")
        self.assertEqual(schedule["method"], c5.NATIVE_WHOLE_UPDATE_PARALLEL)
        self.assertEqual(schedule["history_id"], "07741c45")
        self.assertEqual(schedule["screening_pass_count"], 1)
        self.assertEqual(schedule["episode_ids"], ["07741c45:0", "07741c45:1", "07741c45:2"])
        self.assertEqual(
            [block["concurrency"] for block in schedule["block_schedules"]],
            [1, 2, 4, 8],
        )
        self.assertEqual(
            [block["absolute_arrival_offsets_ns"] for block in schedule["block_schedules"]],
            [[0, 100, 200]] * 4,
        )
        self.assertEqual(
            len({block["graph_namespace"] for block in schedule["block_schedules"]}),
            4,
        )
        self.assertEqual(schedule["payload_sha256"], c5.payload_sha256(schedule))

        for invalid in (0, 3, 16):
            with self.assertRaises(c5.NativeCharacterizationC5Error):
                c5.run_whole_update_parallel(
                    episodes(1),
                    [0],
                    concurrency=invalid,
                    clock=FakeClock(),
                    whole_update_service=FakeWholeUpdate({0: 1}),
                    durable_writer=FakeDurableWriter(),
                )

    def test_parallel_replay_uses_source_order_dispatch_but_allows_out_of_order_visibility(self) -> None:
        writer = FakeDurableWriter()
        result = c5.run_whole_update_parallel(
            episodes(4),
            [0, 0, 0, 0],
            concurrency=2,
            clock=FakeClock(),
            whole_update_service=FakeWholeUpdate({0: 30, 1: 10, 2: 10, 3: 1}),
            durable_writer=writer,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(
            [(item["source_sequence"], item["worker_id"], item["service_start_timestamp_ns"], item["publish_timestamp_ns"]) for item in result["records"]],
            [(1, 1, 0, 10), (2, 1, 10, 20), (3, 1, 20, 21), (0, 0, 0, 30)],
        )
        self.assertEqual([item["source_sequence"] for item in writer.publications], [1, 2, 3, 0])
        self.assertEqual(result["aggregate"]["makespan_ns"], 30)
        self.assertAlmostEqual(result["aggregate"]["throughput_episodes_per_second"], 4 * 1e9 / 30)
        self.assertEqual(result["invariants"]["source_order_violation_count"], 1)
        self.assertEqual(result["interpretation"], c5.DIRECT_INVARIANT_VIOLATION_OBSERVED)

    def test_invariant_checker_counts_loss_duplicate_publication_and_temporal_failures(self) -> None:
        records = [
            {
                "source_sequence": 0,
                "arrival_timestamp_ns": 0,
                "service_start_timestamp_ns": 0,
                "publish_timestamp_ns": 10,
                "caller_return_timestamp_ns": 10,
                "transaction_status": "committed",
            },
            {
                "source_sequence": 0,
                "arrival_timestamp_ns": 5,
                "service_start_timestamp_ns": 4,
                "publish_timestamp_ns": 3,
                "caller_return_timestamp_ns": 3,
                "transaction_status": "committed",
            },
        ]

        invariants = c5.check_c5_invariants(
            episodes(3),
            records,
            graph_parity={"canonical_graph_sha256_match": False, "oracle_miss_count": 2},
            retrieval_parity={"retrieval_result_sha256_match": False, "oracle_miss_count": 1},
        )

        self.assertEqual(invariants["requested_episode_count"], 3)
        self.assertEqual(invariants["published_episode_count"], 2)
        self.assertEqual(invariants["lost_episode_count"], 2)
        self.assertEqual(invariants["duplicate_episode_count"], 1)
        self.assertEqual(invariants["publication_loss_count"], 2)
        self.assertEqual(invariants["temporal_invariant_violation_count"], 1)
        self.assertEqual(invariants["source_order_violation_count"], 0)
        self.assertEqual(invariants["graph_parity_mismatch"], True)
        self.assertEqual(invariants["retrieval_parity_mismatch"], True)

    def test_oracle_miss_alone_is_confounded_not_direct_invariant(self) -> None:
        records = [
            {
                "source_sequence": 0,
                "arrival_timestamp_ns": 0,
                "service_start_timestamp_ns": 0,
                "publish_timestamp_ns": 10,
                "caller_return_timestamp_ns": 10,
                "transaction_status": "committed",
            }
        ]
        invariants = c5.check_c5_invariants(
            episodes(1),
            records,
            graph_parity={"canonical_graph_sha256_match": True, "oracle_miss_count": 1},
            retrieval_parity={"retrieval_result_sha256_match": True, "oracle_miss_count": 0},
            model_outputs_fixed=False,
        )

        self.assertEqual(invariants["direct_invariant_violation_count"], 0)
        self.assertEqual(
            c5.interpret_c5_screening([{"invariants": invariants, "service_error_count": 0}]),
            c5.OUTCOME_INSTABILITY_OR_CONFOUNDED,
        )

    def test_transaction_failure_is_checkpointed_once_sanitized_and_interpreted_as_direct_evidence(self) -> None:
        writer = FakeDurableWriter()
        result = c5.run_whole_update_parallel(
            episodes(3),
            [0, 0, 0],
            concurrency=2,
            clock=FakeClock(),
            whole_update_service=FakeWholeUpdate({0: 10, 1: 20, 2: 1}, transaction_fail_at=1),
            durable_writer=writer,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(writer.failures), 1)
        checkpoint = writer.failures[0]
        self.assertEqual(checkpoint["failed_source_sequence"], 1)
        self.assertEqual(checkpoint["error_class"], "TransactionFailure")
        self.assertEqual(checkpoint["transaction_error_count"], 1)
        self.assertNotIn("secret-token", repr(checkpoint))
        self.assertEqual(result["interpretation"], c5.DIRECT_INVARIANT_VIOLATION_OBSERVED)

    def test_artifact_store_requires_four_completed_block_checkpoints_before_result(self) -> None:
        schedule = c5.build_c5_schedule(
            run_id="c5-0123456789abcdef",
            history_id="07741c45",
            episode_source_hashes=["a" * 64],
            interarrival_ns=100,
        )
        with tempfile.TemporaryDirectory() as temporary:
            store = c5.C5ArtifactStore.create(
                Path(temporary) / "runs",
                "c5-0123456789abcdef",
                schedule,
                provenance_hashes={"c4_result_sha256": "1" * 64},
                command_argv=["native-characterization-c5", "--offline"],
            )
            for block_index, concurrency in enumerate(c5.CONCURRENCY_GRID):
                block_result_for_concurrency = c5.analyze_c5_block(
                    concurrency=concurrency,
                    expected_episode_ids=["07741c45:0"],
                    publication_records=[
                        {
                            "event_sequence": 0,
                            "source_sequence": 0,
                            "episode_id": "07741c45:0",
                            "arrival_timestamp_ns": 0,
                            "service_start_timestamp_ns": 0,
                            "publish_timestamp_ns": 10,
                            "worker_id": f"worker-{block_index}",
                            "work_counts": {"llm_calls": 1},
                        }
                    ],
                    canonical_graph_parity={"status": "pass"},
                    retrieval_parity={"status": "pass"},
                    execution_path_evidence={"treatment_is_concurrency_only": True},
                )
                store.write_block_checkpoint(
                    block_index=block_index,
                    status="completed",
                    result=block_result_for_concurrency,
                )
            result = store.write_e4_result()

            self.assertEqual(result["schema_version"], c5.RESULT_SCHEMA)
            self.assertEqual(result["completed_block_count"], 4)
            self.assertEqual(
                result["overall_interpretation"],
                c5.NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED,
            )
            self.assertEqual(c5.verify_c5_artifacts(store.run_dir)["attempt_status"], "complete")

    def test_failed_block_checkpoint_can_never_be_finalized_as_complete(self) -> None:
        schedule = c5.build_c5_schedule(
            run_id="c5-0123456789abcdef",
            history_id="07741c45",
            episode_source_hashes=["a" * 64],
            interarrival_ns=0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            store = c5.C5ArtifactStore.create(
                Path(temporary) / "runs",
                "c5-0123456789abcdef",
                schedule,
                provenance_hashes={"freeze_sha256": "1" * 64},
                command_argv=["native-characterization-c5", "--dry-run"],
            )
            result = c5.analyze_c5_block(
                concurrency=1,
                expected_episode_ids=["07741c45:0"],
                publication_records=[
                    {
                        "event_sequence": 0,
                        "source_sequence": 0,
                        "arrival_timestamp_ns": 0,
                        "service_start_timestamp_ns": 0,
                        "publish_timestamp_ns": 1,
                    }
                ],
                canonical_graph_parity={"status": "pass"},
                retrieval_parity={"status": "pass"},
                execution_path_evidence={"treatment_is_concurrency_only": True},
            )
            for block_index in range(4):
                store.write_block_checkpoint(
                    block_index=block_index,
                    status="failed" if block_index == 2 else "completed",
                    result=result,
                )

            with self.assertRaisesRegex(
                c5.NativeCharacterizationC5Error,
                "block_checkpoint_not_completed",
            ):
                store.write_e4_result()
            self.assertFalse(store.result_path.exists())

    def test_verifier_rejects_result_whose_checkpoint_hash_closure_drifted(self) -> None:
        schedule = c5.build_c5_schedule(
            run_id="c5-0123456789abcdef",
            history_id="07741c45",
            episode_source_hashes=["a" * 64],
            interarrival_ns=0,
        )
        with tempfile.TemporaryDirectory() as temporary:
            store = c5.C5ArtifactStore.create(
                Path(temporary) / "runs",
                "c5-0123456789abcdef",
                schedule,
                provenance_hashes={"freeze_sha256": "1" * 64},
                command_argv=["native-characterization-c5", "--dry-run"],
            )
            for block_index, concurrency in enumerate(c5.CONCURRENCY_GRID):
                result = c5.analyze_c5_block(
                    concurrency=concurrency,
                    expected_episode_ids=["07741c45:0"],
                    publication_records=[
                        {
                            "event_sequence": 0,
                            "source_sequence": 0,
                            "arrival_timestamp_ns": 0,
                            "service_start_timestamp_ns": 0,
                            "publish_timestamp_ns": 1,
                        }
                    ],
                    canonical_graph_parity={"status": "pass"},
                    retrieval_parity={"status": "pass"},
                    execution_path_evidence={"treatment_is_concurrency_only": True},
                )
                store.write_block_checkpoint(
                    block_index=block_index,
                    status="completed",
                    result=result,
                )
            store.write_e4_result()
            checkpoint_path = store.run_dir / "blocks/003/checkpoint.json"
            checkpoint = json.loads(checkpoint_path.read_text("ascii"))
            checkpoint["result"]["bounded_claim"] = "drift"
            checkpoint_path.write_text(
                json.dumps(checkpoint, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="ascii",
            )

            verification = c5.verify_c5_artifacts(store.run_dir)
            self.assertEqual(
                verification["attempt_status"],
                "incomplete_invalid_non_mergeable",
            )

    def test_infrastructure_error_is_never_a_scientific_interpretation(self) -> None:
        with self.assertRaisesRegex(
            c5.NativeCharacterizationC5Error,
            "infrastructure_failure_not_scientific_result",
        ):
            c5.interpret_c5_screening(
                [
                    {
                        "invariants": {
                            "direct_invariant_violation_count": 0,
                            "confounded_evidence_count": 0,
                        },
                        "service_error_count": 1,
                    }
                ]
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
