"""Focused durability contracts for the C4/E3 artifact layer.

These tests deliberately use synthetic schedule and timing data.  They test
crash consistency, provenance binding, sanitization, and verification without
contacting Graphiti, Neo4j, or either model service.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_c4_artifacts as c4a  # noqa: E402
from native_characterization_c4_schedule import derive_c4_schedule  # noqa: E402


RUN_ID = "c4-0123456789abcdef"
LOADS = (0.5, 0.8, 1.0, 1.2, 1.5)
METHODS = ("Native-Sync", "Native-Async-Serial")


def _schedule() -> dict[str, object]:
    blocks = []
    for block_index, (method, load) in enumerate(
        (method, load) for method in METHODS for load in LOADS
    ):
        blocks.append(
            {
                "block_index": block_index,
                "graph_namespace": f"nc-e3-{block_index:016x}",
                "history_id": "07741c45",
                "method": method,
                "normalized_offered_load": load,
                "interarrival_ns": 1000 - block_index,
                "absolute_arrival_offsets_ns": [
                    source_sequence * (1000 - block_index)
                    for source_sequence in range(49)
                ],
            }
        )
    return c4a.seal_payload(
        {
            "schema_version": "membind.native-characterization-c4-schedule-dry-run.v1",
            "status": "dry_run",
            "stage": "C4/E3_OFFLINE_SCHEDULE",
            "run_id": "c2-17cdaabd562e9673",
            "history_id": "07741c45",
            "episode_ids": [f"07741c45:{index}" for index in range(49)],
            "block_schedules": blocks,
        }
    )


def _provenance() -> dict[str, str]:
    return {
        name: hashlib.sha256(name.encode("ascii")).hexdigest()
        for name in c4a.REQUIRED_PROVENANCE_HASHES
    }


def _success_blocks(schedule: dict[str, object] | None = None) -> list[dict[str, object]]:
    selected = _schedule() if schedule is None else schedule
    results = []
    for block in selected["block_schedules"]:
        results.append(
            {
                "block_index": block["block_index"],
                "graph_namespace": block["graph_namespace"],
                "history_id": selected["history_id"],
                "method": block["method"],
                "normalized_offered_load": block["normalized_offered_load"],
            }
        )
    return results


def _append_success_events(
    store: c4a.C4ArtifactStore,
    schedule: dict[str, object],
    *,
    publication_sequences: dict[int, list[int]] | None = None,
    omit_async_enqueue: tuple[int, int] | None = None,
) -> dict[tuple[int, int], str]:
    publication_hashes: dict[tuple[int, int], str] = {}
    for block in schedule["block_schedules"]:
        block_index = block["block_index"]
        method = block["method"]
        sequences = (
            list(range(49))
            if publication_sequences is None
            else publication_sequences.get(block_index, list(range(49)))
        )
        for source_sequence in sequences:
            episode_id = f"{schedule['history_id']}:{source_sequence}"
            base = (
                block_index * 10_000_000
                + source_sequence * block["interarrival_ns"]
            )
            if method == "Native-Async-Serial" and omit_async_enqueue != (
                block_index,
                source_sequence,
            ):
                store.append_enqueue_event(
                    {
                        "block_index": block_index,
                        "source_sequence": source_sequence,
                        "episode_id": episode_id,
                        "graph_namespace": block["graph_namespace"],
                        "method": method,
                        "arrival_timestamp_ns": base,
                    }
                )
            publication = store.append_publication_event(
                {
                    "block_index": block_index,
                    "source_sequence": source_sequence,
                    "episode_id": episode_id,
                    "scheduled_arrival_timestamp_ns": base,
                    "arrival_timestamp_ns": base,
                    "enqueue_ack_timestamp_ns": base if method == "Native-Sync" else base + 1,
                    "service_start_timestamp_ns": base + 2,
                    "publish_timestamp_ns": base + 10,
                    "caller_return_timestamp_ns": (
                        base + 10 if method == "Native-Sync" else base + 1
                    ),
                }
            )
            publication_hashes[(block_index, source_sequence)] = publication[
                "payload_sha256"
            ]
    return publication_hashes


def _write_success_checkpoints(
    store: c4a.C4ArtifactStore,
    schedule: dict[str, object],
    publication_hashes: dict[tuple[int, int], str],
    *,
    omit_episode: tuple[int, int] | None = None,
    omit_block: int | None = None,
) -> None:
    for block in schedule["block_schedules"]:
        block_index = block["block_index"]
        for source_sequence in range(49):
            if omit_episode == (block_index, source_sequence):
                continue
            store.write_episode_checkpoint(
                block_index=block_index,
                source_sequence=source_sequence,
                status="completed",
                progress={
                    "episode_id": f"{schedule['history_id']}:{source_sequence}",
                    "graph_namespace": block["graph_namespace"],
                    "method": block["method"],
                    "publication_event_payload_sha256": publication_hashes[
                        (block_index, source_sequence)
                    ],
                },
            )
        if omit_block == block_index:
            continue
        store.write_block_checkpoint(
            block_index=block_index,
            status="completed",
            progress={
                "graph_namespace": block["graph_namespace"],
                "history_id": schedule["history_id"],
                "method": block["method"],
                "normalized_offered_load": block["normalized_offered_load"],
                "completed_source_sequences": list(range(49)),
                "completed_episode_count": 49,
            },
        )


def _populate_success_evidence(
    store: c4a.C4ArtifactStore, schedule: dict[str, object]
) -> None:
    publications = _append_success_events(store, schedule)
    _write_success_checkpoints(store, schedule, publications)


class SecretFailure(RuntimeError):
    pass


class NativeCharacterizationC4ArtifactTests(TestCase):
    maxDiff = None

    def test_new_run_directory_rejects_nonempty_and_accepts_existing_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            nonempty = runs / RUN_ID
            nonempty.mkdir(parents=True)
            (nonempty / "foreign.txt").write_text("do not overwrite", encoding="ascii")

            with self.assertRaisesRegex(c4a.NativeCharacterizationC4ArtifactError, "run_directory_nonempty"):
                c4a.C4ArtifactStore.create(
                    runs, RUN_ID, _schedule(), _provenance(), ["c4-runner", "--planned"]
                )
            self.assertEqual((nonempty / "foreign.txt").read_text("ascii"), "do not overwrite")

            empty_id = "c4-fedcba9876543210"
            (runs / empty_id).mkdir()
            store = c4a.C4ArtifactStore.create(
                runs, empty_id, _schedule(), _provenance(), ["c4-runner", "--planned"]
            )
            self.assertTrue(store.manifest_path.is_file())

    def test_planned_manifest_is_canonical_sealed_and_binds_exact_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs",
                RUN_ID,
                _schedule(),
                _provenance(),
                ["c4-runner", "--run-id", RUN_ID],
            )
            raw = store.manifest_path.read_bytes()
            manifest = json.loads(raw.decode("ascii"))

            self.assertEqual(raw, c4a.canonical_json_bytes(manifest) + b"\n")
            self.assertEqual(manifest["payload_sha256"], c4a.payload_sha256(manifest))
            self.assertEqual(manifest["status"], "planned")
            self.assertEqual(manifest["schedule_payload_sha256"], _schedule()["payload_sha256"])
            self.assertEqual(manifest["provenance_hashes"], _provenance())
            self.assertEqual(set(manifest["provenance_hashes"]), set(c4a.REQUIRED_PROVENANCE_HASHES))
            self.assertEqual(len(manifest["planned_blocks"]), 10)
            self.assertEqual(
                [item["block_index"] for item in manifest["planned_blocks"]],
                list(range(10)),
            )

    def test_manifest_rejects_unsealed_schedule_wrong_grid_and_missing_hash(self) -> None:
        valid = _schedule()
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            unsealed = dict(valid)
            unsealed["payload_sha256"] = "0" * 64
            with self.assertRaisesRegex(c4a.NativeCharacterizationC4ArtifactError, "schedule_seal_invalid"):
                c4a.C4ArtifactStore.create(runs, RUN_ID, unsealed, _provenance(), ["runner"])

        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            wrong_grid = dict(valid)
            wrong_grid["block_schedules"] = list(valid["block_schedules"])[:-1]
            wrong_grid = c4a.seal_payload(wrong_grid)
            with self.assertRaisesRegex(c4a.NativeCharacterizationC4ArtifactError, "schedule_blocks_invalid"):
                c4a.C4ArtifactStore.create(runs, RUN_ID, wrong_grid, _provenance(), ["runner"])

        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            incomplete = _provenance()
            incomplete.pop("c3_e2_sha256")
            with self.assertRaisesRegex(c4a.NativeCharacterizationC4ArtifactError, "provenance_hashes_invalid"):
                c4a.C4ArtifactStore.create(runs, RUN_ID, valid, incomplete, ["runner"])

    def test_real_retained_schedule_shape_is_accepted_without_block_history_duplication(self) -> None:
        schedule = derive_c4_schedule(ROOT, "c2-17cdaabd562e9673")
        self.assertNotIn("history_id", schedule["block_schedules"][0])
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs",
                RUN_ID,
                schedule,
                _provenance(),
                ["c4-runner", "--planned"],
            )
            manifest = json.loads(store.manifest_path.read_text("ascii"))

        self.assertEqual(manifest["history_id"], "07741c45")
        self.assertTrue(
            all(item["history_id"] == "07741c45" for item in manifest["planned_blocks"])
        )

    def test_jsonl_events_are_canonical_sealed_ordered_and_fsynced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with patch.object(c4a.os, "fsync", wraps=c4a.os.fsync) as fsync:
                enqueue = store.append_enqueue_event(
                    {
                        "block_index": 0,
                        "source_sequence": 0,
                        "arrival_timestamp_ns": 100,
                        "episode_source_sha256": "a" * 64,
                    }
                )
                publication = store.append_publication_event(
                    {
                        "block_index": 0,
                        "source_sequence": 0,
                        "service_start_timestamp_ns": 101,
                        "publish_timestamp_ns": 200,
                    }
                )
                failure = store.append_failure_event(
                    {
                        "block_index": 0,
                        "source_sequence": 1,
                        "failure_scope": "episode",
                        "error_class": "openai.APIConnectionError",
                        "token_envelope": c4a.nullable_token_envelope(),
                        "status": "incomplete_invalid_non_mergeable",
                    }
                )

            self.assertGreaterEqual(fsync.call_count, 3)
            self.assertEqual(
                [enqueue["event_sequence"], publication["event_sequence"], failure["event_sequence"]],
                [0, 1, 2],
            )
            lines = store.events_path.read_bytes().splitlines()
            parsed = [json.loads(line.decode("ascii")) for line in lines]
            self.assertEqual(
                [item["event_type"] for item in parsed],
                ["enqueue", "publication", "failure"],
            )
            self.assertTrue(all(line == c4a.canonical_json_bytes(item) for line, item in zip(lines, parsed)))
            self.assertTrue(all(item["payload_sha256"] == c4a.payload_sha256(item) for item in parsed))

            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "enqueue_event_future_ack_invalid",
            ):
                store.append_enqueue_event(
                    {
                        "block_index": 0,
                        "source_sequence": 1,
                        "arrival_timestamp_ns": 200,
                        "enqueue_ack_timestamp_ns": 201,
                    }
                )

    def test_events_fail_closed_when_planned_manifest_is_missing_or_tampered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            store.manifest_path.unlink()
            with self.assertRaisesRegex(c4a.NativeCharacterizationC4ArtifactError, "planned_manifest_invalid"):
                store.append_enqueue_event({"block_index": 0, "source_sequence": 0})

    def test_episode_block_and_root_checkpoints_use_atomic_canonical_seals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with patch.object(c4a.os, "fsync", wraps=c4a.os.fsync) as fsync:
                episode_path = store.write_episode_checkpoint(
                    block_index=0,
                    source_sequence=0,
                    status="completed",
                    progress={"completed_source_sequences": [0]},
                )
                block_path = store.write_block_checkpoint(
                    block_index=0,
                    status="completed",
                    progress={"completed_source_sequences": [0, 1]},
                )
                root_path = store.write_root_checkpoint(
                    status="running",
                    progress={"completed_block_indices": [0]},
                )

            # Each atomic replacement fsyncs both file content and its directory.
            self.assertGreaterEqual(fsync.call_count, 6)

            for path, level in (
                (episode_path, "episode"),
                (block_path, "block"),
                (root_path, "root"),
            ):
                raw = path.read_bytes()
                value = json.loads(raw.decode("ascii"))
                self.assertEqual(raw, c4a.canonical_json_bytes(value) + b"\n")
                self.assertEqual(value["checkpoint_level"], level)
                self.assertEqual(value["payload_sha256"], c4a.payload_sha256(value))
            self.assertFalse(any(path.name.endswith(".tmp") for path in store.run_root.rglob("*")))

    def test_failure_checkpoint_discards_exception_message_and_has_nullable_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            secret = "Bearer super-secret-api-key"
            result = store.record_failure(
                block_index=3,
                source_sequence=7,
                error=SecretFailure(secret),
                completed_source_sequences=[0, 1, 2, 3, 4, 5, 6],
                completed_block_indices=[0, 1, 2],
                completed_episode_count=154,
                token_envelope={
                    "prompt_tokens": 25001,
                    "output_tokens": None,
                    "requested_max_tokens": 16384,
                },
            )
            root = json.loads(store.root_checkpoint_path.read_text("ascii"))
            encoded_run = b"".join(path.read_bytes() for path in sorted(store.run_root.rglob("*")) if path.is_file())

            self.assertEqual(result["status"], "incomplete_invalid_non_mergeable")
            self.assertEqual(root["status"], "incomplete_invalid_non_mergeable")
            self.assertEqual(root["failure"]["error_class"], f"{__name__}.SecretFailure")
            self.assertEqual(
                root["failure"]["token_envelope"],
                {
                    "prompt_tokens": 25001,
                    "output_tokens": None,
                    "requested_max_tokens": 16384,
                },
            )
            self.assertNotIn(secret.encode("ascii"), encoded_run)
            self.assertNotIn(b"exception_message", encoded_run)
            self.assertNotIn(b"error_message", encoded_run)

    def test_failure_distinguishes_global_completed_count_from_block_local_sequences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "failure_completed_episode_count_invalid",
            ):
                store.record_failure(
                    block_index=3,
                    source_sequence=7,
                    error=RuntimeError("not persisted"),
                    completed_source_sequences=list(range(7)),
                    completed_block_indices=[0, 1, 2],
                    completed_episode_count=153,
                )
            result = store.record_failure(
                block_index=3,
                source_sequence=7,
                error=RuntimeError("not persisted"),
                completed_source_sequences=list(range(7)),
                completed_block_indices=[0, 1, 2],
                completed_episode_count=154,
            )
            root = json.loads(store.root_checkpoint_path.read_text("ascii"))
            block = json.loads(
                (store.run_root / "blocks/003/checkpoint.json").read_text("ascii")
            )

            self.assertEqual(result["completed_episode_count"], 154)
            self.assertEqual(result["event_sequence"], 0)
            self.assertEqual(root["progress"]["completed_episode_count"], 154)
            self.assertEqual(block["progress"]["completed_source_sequences"], list(range(7)))
            self.assertNotIn("completed_source_sequences", root["progress"])
            self.assertEqual(
                c4a.verify_c4_artifacts(store.run_root)["attempt_status"],
                "incomplete_invalid_non_mergeable",
            )

            root["progress"]["completed_episode_count"] = 7
            root = c4a.seal_payload(root)
            store.root_checkpoint_path.write_bytes(c4a.canonical_json_bytes(root) + b"\n")
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "failure_checkpoint_contract_invalid",
            ):
                c4a.verify_c4_artifacts(store.run_root)

    def test_prepare_resume_prefix_rolls_back_partial_block_to_full_block_boundary(self) -> None:
        schedule = _schedule()
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            store = c4a.C4ArtifactStore.create(
                runs, RUN_ID, schedule, _provenance(), ["runner"]
            )
            publication_sequences = {
                **{block_index: list(range(49)) for block_index in range(3)},
                3: list(range(9)),
                **{block_index: [] for block_index in range(4, 10)},
            }
            publications = _append_success_events(
                store, schedule, publication_sequences=publication_sequences
            )
            for block in schedule["block_schedules"][:3]:
                block_index = block["block_index"]
                for source_sequence in range(49):
                    store.write_episode_checkpoint(
                        block_index=block_index,
                        source_sequence=source_sequence,
                        status="completed",
                        progress={
                            "episode_id": f"{schedule['history_id']}:{source_sequence}",
                            "graph_namespace": block["graph_namespace"],
                            "method": block["method"],
                            "publication_event_payload_sha256": publications[
                                (block_index, source_sequence)
                            ],
                        },
                    )
                store.write_block_checkpoint(
                    block_index=block_index,
                    status="completed",
                    progress={
                        "graph_namespace": block["graph_namespace"],
                        "history_id": schedule["history_id"],
                        "method": block["method"],
                        "normalized_offered_load": block["normalized_offered_load"],
                        "completed_source_sequences": list(range(49)),
                        "completed_episode_count": 49,
                    },
                )
            block = schedule["block_schedules"][3]
            for source_sequence in range(9):
                store.write_episode_checkpoint(
                    block_index=3,
                    source_sequence=source_sequence,
                    status="completed",
                    progress={
                        "episode_id": f"{schedule['history_id']}:{source_sequence}",
                        "graph_namespace": block["graph_namespace"],
                        "method": block["method"],
                        "publication_event_payload_sha256": publications[
                            (3, source_sequence)
                        ],
                    },
                )
            store.write_root_checkpoint(
                status="running",
                progress={
                    "completed_block_indices": [0, 1, 2],
                    "completed_block_count": 3,
                    "completed_episode_count": 147,
                },
            )

            before = c4a.verify_c4_artifacts(store.run_root)
            prefix = c4a.prepare_c4_resume_prefix(
                runs_root=runs,
                run_id=RUN_ID,
                schedule=schedule,
                provenance_hashes=_provenance(),
            )
            after = c4a.verify_c4_artifacts(store.run_root)
            reopened = c4a.C4ArtifactStore.open_existing_for_resume(runs, RUN_ID)

        self.assertEqual(before["attempt_status"], "running")
        self.assertEqual(before["event_counts"]["publication"], 156)
        self.assertEqual(prefix["completed_block_indices"], [0, 1, 2])
        self.assertEqual(prefix["next_block_index"], 3)
        self.assertEqual(prefix["completed_episode_count"], 147)
        self.assertEqual(prefix["discarded_event_count"], 9)
        self.assertEqual(prefix["discarded_checkpoint_count"], 9)
        self.assertEqual(after["attempt_status"], "running")
        self.assertEqual(after["event_counts"]["publication"], 147)
        self.assertEqual(after["checkpoint_count"], 151)
        self.assertIn("resume_rollback_audit.json", after["hash_inventory"])
        self.assertFalse((reopened.run_root / "blocks/003").exists())
        self.assertEqual(reopened._next_event_sequence, 147)

    def test_recover_terminal_failure_restores_running_full_block_resume_prefix(self) -> None:
        schedule = _schedule()
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            store = c4a.C4ArtifactStore.create(
                runs, RUN_ID, schedule, _provenance(), ["runner"]
            )
            publication_sequences = {
                **{block_index: list(range(49)) for block_index in range(3)},
                3: list(range(8)),
                **{block_index: [] for block_index in range(4, 10)},
            }
            publications = _append_success_events(
                store, schedule, publication_sequences=publication_sequences
            )
            for block in schedule["block_schedules"][:3]:
                block_index = block["block_index"]
                for source_sequence in range(49):
                    store.write_episode_checkpoint(
                        block_index=block_index,
                        source_sequence=source_sequence,
                        status="completed",
                        progress={
                            "episode_id": f"{schedule['history_id']}:{source_sequence}",
                            "graph_namespace": block["graph_namespace"],
                            "method": block["method"],
                            "publication_event_payload_sha256": publications[
                                (block_index, source_sequence)
                            ],
                        },
                    )
                store.write_block_checkpoint(
                    block_index=block_index,
                    status="completed",
                    progress={
                        "graph_namespace": block["graph_namespace"],
                        "history_id": schedule["history_id"],
                        "method": block["method"],
                        "normalized_offered_load": block["normalized_offered_load"],
                        "completed_source_sequences": list(range(49)),
                        "completed_episode_count": 49,
                    },
                )
            block = schedule["block_schedules"][3]
            for source_sequence in range(8):
                store.write_episode_checkpoint(
                    block_index=3,
                    source_sequence=source_sequence,
                    status="completed",
                    progress={
                        "episode_id": f"{schedule['history_id']}:{source_sequence}",
                        "graph_namespace": block["graph_namespace"],
                        "method": block["method"],
                        "publication_event_payload_sha256": publications[
                            (3, source_sequence)
                        ],
                    },
                )
            store.record_failure(
                block_index=3,
                source_sequence=8,
                error=RuntimeError("must not persist raw detail"),
                completed_source_sequences=list(range(8)),
                completed_block_indices=[0, 1, 2],
                completed_episode_count=155,
                token_envelope={
                    "prompt_tokens": 25001,
                    "output_tokens": None,
                    "requested_max_tokens": 16384,
                },
            )

            before = c4a.verify_c4_artifacts(store.run_root)
            prefix = c4a.recover_c4_terminal_failure_to_resume_prefix(
                runs_root=runs,
                run_id=RUN_ID,
                schedule=schedule,
                provenance_hashes=_provenance(),
            )
            after = c4a.verify_c4_artifacts(store.run_root)
            audit = json.loads(
                (store.run_root / "resume_rollback_audit.json").read_text("ascii")
            )
            reopened = c4a.C4ArtifactStore.open_existing_for_resume(runs, RUN_ID)

        self.assertEqual(before["attempt_status"], c4a.FAILURE_STATUS)
        self.assertEqual(before["event_counts"], {"enqueue": 0, "publication": 155, "failure": 1})
        self.assertEqual(prefix["completed_block_indices"], [0, 1, 2])
        self.assertEqual(prefix["next_block_index"], 3)
        self.assertEqual(prefix["completed_episode_count"], 147)
        self.assertEqual(prefix["discarded_event_count"], 9)
        self.assertEqual(prefix["discarded_checkpoint_count"], 10)
        self.assertEqual(prefix["recovered_terminal_failure"], True)
        self.assertEqual(after["attempt_status"], "running")
        self.assertEqual(after["event_counts"], {"enqueue": 0, "publication": 147, "failure": 0})
        self.assertEqual(after["checkpoint_count"], 151)
        self.assertEqual(audit["status"], "terminal_failure_recovered_to_completed_block_prefix")
        self.assertEqual(audit["failure_block_index"], 3)
        self.assertEqual(audit["failure_source_sequence"], 8)
        self.assertEqual(audit["terminal_completed_episode_count"], 155)
        self.assertFalse((reopened.run_root / "blocks/003").exists())
        self.assertEqual(reopened._next_event_sequence, 147)

    def test_episode_failure_requires_exact_fifo_prefix_and_global_equality(self) -> None:
        invalid_progress = (
            {
                "block_index": 3,
                "source_sequence": 7,
                "completed_source_sequences": [0, 1, 3],
                "completed_block_indices": [0, 1, 2],
                "completed_episode_count": 150,
            },
            {
                "block_index": 3,
                "source_sequence": 7,
                "completed_source_sequences": list(range(7)),
                "completed_block_indices": [0, 2],
                "completed_episode_count": 105,
            },
            {
                "block_index": 3,
                "source_sequence": 7,
                "completed_source_sequences": list(range(7)),
                "completed_block_indices": [0, 1, 2],
                "completed_episode_count": 155,
            },
            {
                "block_index": 0,
                "source_sequence": 49,
                "completed_source_sequences": list(range(49)),
                "completed_block_indices": [],
                "completed_episode_count": 49,
            },
        )
        for index, progress in enumerate(invalid_progress):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                store = c4a.C4ArtifactStore.create(
                    Path(temporary) / "runs",
                    RUN_ID,
                    _schedule(),
                    _provenance(),
                    ["runner"],
                )
                with self.assertRaises(c4a.NativeCharacterizationC4ArtifactError):
                    store.record_failure(
                        error=RuntimeError("not persisted"),
                        **progress,
                    )

    def test_sensitive_fields_and_invalid_token_envelopes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with self.assertRaisesRegex(c4a.NativeCharacterizationC4ArtifactError, "artifact_not_sanitized"):
                store.append_enqueue_event(
                    {"block_index": 0, "source_sequence": 0, "api_key": "secret"}
                )
            with self.assertRaisesRegex(c4a.NativeCharacterizationC4ArtifactError, "token_envelope_invalid"):
                store.record_failure(
                    block_index=0,
                    source_sequence=0,
                    error=RuntimeError("ignored"),
                    completed_source_sequences=[],
                    completed_block_indices=[],
                    completed_episode_count=0,
                    token_envelope={"prompt_tokens": -1},
                )

    def test_success_finalization_keeps_manifest_immutable_and_binds_490_episodes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with patch.object(c4a.os, "fsync"):
                _populate_success_evidence(store, _schedule())
            manifest_before = store.manifest_path.read_bytes()
            summary = store.finalize_success(_success_blocks())
            manifest_after = store.manifest_path.read_bytes()
            root = json.loads(store.root_checkpoint_path.read_text("ascii"))
            persisted = json.loads(store.success_summary_path.read_text("ascii"))

            self.assertEqual(manifest_before, manifest_after)
            self.assertEqual(summary, persisted)
            self.assertEqual(summary["schema_version"], c4a.SUCCESS_SCHEMA)
            self.assertEqual(summary["status"], "complete")
            self.assertFalse(summary["mergeable"])
            self.assertEqual(summary["screening_repetition_count"], 1)
            self.assertEqual(summary["block_count"], 10)
            self.assertEqual(summary["episode_count"], 490)
            self.assertEqual(len(summary["block_results"]), 10)
            self.assertEqual(summary["durable_evidence"]["publication_count"], 490)
            self.assertEqual(summary["durable_evidence"]["enqueue_count"], 245)
            self.assertEqual(summary["durable_evidence"]["episode_checkpoint_count"], 490)
            self.assertEqual(summary["durable_evidence"]["block_checkpoint_count"], 10)
            self.assertEqual(len(summary["block_results"][0]["episode_metrics"]), 49)
            self.assertEqual(
                summary["block_results"][0]["episode_metrics"][0]["schedule_lag_ns"],
                0,
            )
            self.assertEqual(
                summary["block_results"][0]["aggregate"]["mean_construction_service_time_ns"],
                8,
            )
            self.assertEqual(
                summary["block_results"][5]["aggregate"]["mean_post_return_stale_window_ns"],
                9,
            )
            self.assertEqual(root["status"], "completed")
            self.assertEqual(root["progress"]["completed_block_indices"], list(range(10)))
            self.assertEqual(root["progress"]["completed_episode_count"], 490)
            self.assertEqual(root["progress"]["success_summary_payload_sha256"], summary["payload_sha256"])
            self.assertEqual(
                root["progress"]["success_summary_sha256"],
                hashlib.sha256(store.success_summary_path.read_bytes()).hexdigest(),
            )

    def test_success_finalization_rejects_incomplete_grid_and_failure_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError, "success_block_results_invalid"
            ):
                store.finalize_success(_success_blocks()[:-1])
            store.record_failure(
                block_index=0,
                source_sequence=0,
                error=RuntimeError("not persisted"),
                completed_source_sequences=[],
                completed_block_indices=[],
                completed_episode_count=0,
            )
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError, "success_after_failure_invalid"
            ):
                store.finalize_success(_success_blocks())

    def test_fresh_store_cannot_finalize_from_fabricated_ten_block_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "success_durable_evidence_invalid",
            ):
                store.finalize_success(_success_blocks())

    def test_caller_supplied_aggregate_is_not_accepted_as_success_evidence(self) -> None:
        fabricated = _success_blocks()
        fabricated[0]["aggregate"] = {"throughput_episodes_per_second": 1e30}
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "success_block_results_invalid",
            ):
                store.finalize_success(fabricated)

    def test_finalize_rejects_publication_timestamp_or_caller_ack_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with patch.object(c4a.os, "fsync"):
                _append_success_events(store, _schedule())
            records = [
                json.loads(line)
                for line in store.events_path.read_text("ascii").splitlines()
            ]
            target = next(
                record
                for record in records
                if record["event_type"] == "publication"
                and record["block_index"] == 5
                and record["source_sequence"] == 0
            )
            target["caller_return_timestamp_ns"] += 1
            target.update(c4a.seal_payload(target))
            store.events_path.write_bytes(
                b"\n".join(c4a.canonical_json_bytes(record) for record in records)
                + b"\n"
            )
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "success_durable_evidence_invalid",
            ):
                store.finalize_success(_success_blocks())

    def test_finalize_rejects_missing_duplicate_or_out_of_order_publication(self) -> None:
        invalid_orders = {
            "missing": {0: list(range(48))},
            "duplicate": {0: list(range(49)) + [48]},
            "out_of_order": {0: [1, 0, *range(2, 49)]},
        }
        for label, order in invalid_orders.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                store = c4a.C4ArtifactStore.create(
                    Path(temporary) / "runs",
                    RUN_ID,
                    _schedule(),
                    _provenance(),
                    ["runner"],
                )
                with patch.object(c4a.os, "fsync"):
                    _append_success_events(
                        store, _schedule(), publication_sequences=order
                    )
                with self.assertRaisesRegex(
                    c4a.NativeCharacterizationC4ArtifactError,
                    "success_durable_evidence_invalid",
                ):
                    store.finalize_success(_success_blocks())

    def test_finalize_rejects_async_enqueue_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with patch.object(c4a.os, "fsync"):
                _append_success_events(
                    store, _schedule(), omit_async_enqueue=(5, 0)
                )
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "success_durable_evidence_invalid",
            ):
                store.finalize_success(_success_blocks())

    def test_finalize_rejects_missing_or_mismatched_episode_and_block_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with patch.object(c4a.os, "fsync"):
                publications = _append_success_events(store, _schedule())
                _write_success_checkpoints(
                    store,
                    _schedule(),
                    publications,
                    omit_episode=(9, 48),
                    omit_block=9,
                )
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "success_durable_evidence_invalid",
            ):
                store.finalize_success(_success_blocks())

            _write_success_checkpoints(
                store,
                _schedule(),
                publications,
            )
            episode_path = store.run_root / "blocks/009/episodes/000048.checkpoint.json"
            episode = json.loads(episode_path.read_text("ascii"))
            episode["progress"]["episode_id"] = "07741c45:wrong"
            episode = c4a.seal_payload(episode)
            episode_path.write_bytes(c4a.canonical_json_bytes(episode) + b"\n")
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "success_durable_evidence_invalid",
            ):
                store.finalize_success(_success_blocks())

    def test_verifier_and_hash_inventory_are_read_only_deterministic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            store.append_enqueue_event({"block_index": 0, "source_sequence": 0})
            store.append_publication_event({"block_index": 0, "source_sequence": 0})
            store.write_root_checkpoint(
                status="running", progress={"completed_block_indices": []}
            )
            store.write_episode_checkpoint(
                block_index=0,
                source_sequence=0,
                status="completed",
                progress={"completed_source_sequences": [0]},
            )
            store.write_block_checkpoint(
                block_index=0,
                status="completed",
                progress={"completed_source_sequences": [0]},
            )

            before = {path.relative_to(store.run_root).as_posix(): path.read_bytes() for path in store.run_root.rglob("*") if path.is_file()}
            first = c4a.verify_c4_artifacts(store.run_root)
            second = c4a.verify_c4_artifacts(store.run_root)
            inventory = c4a.build_hash_inventory(store.run_root)
            after = {path.relative_to(store.run_root).as_posix(): path.read_bytes() for path in store.run_root.rglob("*") if path.is_file()}

            self.assertEqual(first, second)
            self.assertEqual(before, after)
            self.assertEqual(first["status"], "verified")
            self.assertEqual(first["attempt_status"], "running")
            self.assertEqual(first["event_counts"], {"enqueue": 1, "failure": 0, "publication": 1})
            self.assertEqual(first["hash_inventory"], inventory)
            self.assertIn("manifest.json", inventory)
            self.assertIn("schedule.json", inventory)
            self.assertIn("events.jsonl", inventory)
            self.assertTrue(all(len(item["sha256"]) == 64 for item in inventory.values()))
            self.assertEqual(first["checkpoint_count"], 3)
            self.assertEqual(first["payload_sha256"], c4a.payload_sha256(first))

    def test_verifier_distinguishes_running_failed_and_complete_and_binds_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            running = c4a.C4ArtifactStore.create(
                runs, "c4-0000000000000001", _schedule(), _provenance(), ["runner"]
            )
            failed = c4a.C4ArtifactStore.create(
                runs, "c4-0000000000000002", _schedule(), _provenance(), ["runner"]
            )
            complete = c4a.C4ArtifactStore.create(
                runs, "c4-0000000000000003", _schedule(), _provenance(), ["runner"]
            )
            failed.record_failure(
                block_index=0,
                source_sequence=0,
                error=RuntimeError("not persisted"),
                completed_source_sequences=[],
                completed_block_indices=[],
                completed_episode_count=0,
            )
            with patch.object(c4a.os, "fsync"):
                _populate_success_evidence(complete, _schedule())
                complete.finalize_success(_success_blocks())

            self.assertEqual(c4a.verify_c4_artifacts(running.run_root)["attempt_status"], "running")
            self.assertEqual(
                c4a.verify_c4_artifacts(failed.run_root)["attempt_status"],
                "incomplete_invalid_non_mergeable",
            )
            verified = c4a.verify_c4_artifacts(complete.run_root)
            self.assertEqual(verified["attempt_status"], "complete")
            self.assertEqual(
                verified["success_summary_payload_sha256"],
                json.loads(complete.success_summary_path.read_text("ascii"))["payload_sha256"],
            )

            corrupted = json.loads(complete.success_summary_path.read_text("ascii"))
            corrupted["episode_count"] = 489
            corrupted = c4a.seal_payload(corrupted)
            complete.success_summary_path.write_bytes(c4a.canonical_json_bytes(corrupted) + b"\n")
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "success_summary_contract_invalid",
            ):
                c4a.verify_c4_artifacts(complete.run_root)

    def test_verifier_rejects_post_completion_durable_evidence_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with patch.object(c4a.os, "fsync"):
                _populate_success_evidence(store, _schedule())
                store.finalize_success(_success_blocks())
            self.assertEqual(c4a.verify_c4_artifacts(store.run_root)["attempt_status"], "complete")

            episode_path = store.run_root / "blocks/009/episodes/000048.checkpoint.json"
            episode_bytes = episode_path.read_bytes()
            episode_path.unlink()
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "success_durable_evidence_invalid",
            ):
                c4a.verify_c4_artifacts(store.run_root)
            episode_path.write_bytes(episode_bytes)

            block_path = store.run_root / "blocks/009/checkpoint.json"
            block_bytes = block_path.read_bytes()
            block_path.unlink()
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "success_durable_evidence_invalid",
            ):
                c4a.verify_c4_artifacts(store.run_root)
            block_path.write_bytes(block_bytes)

            events = store.events_path.read_bytes().splitlines(keepends=True)
            store.events_path.write_bytes(b"".join(events[:-1]))
            with self.assertRaisesRegex(
                c4a.NativeCharacterizationC4ArtifactError,
                "success_durable_evidence_invalid",
            ):
                c4a.verify_c4_artifacts(store.run_root)

    def test_stage_terminal_failure_is_root_only_and_preserves_490_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with patch.object(c4a.os, "fsync"):
                _populate_success_evidence(store, _schedule())
                event = store.record_stage_failure(
                    failure_stage="finalization",
                    error=RuntimeError("secret finalizer details"),
                    completed_block_indices=list(range(10)),
                    completed_episode_count=490,
                    token_envelope=None,
                )
            root = json.loads(store.root_checkpoint_path.read_text("ascii"))
            verified = c4a.verify_c4_artifacts(store.run_root)

            self.assertEqual(event["failure_scope"], "stage")
            self.assertEqual(event["failure_stage"], "finalization")
            self.assertIsNone(event["block_index"])
            self.assertIsNone(event["source_sequence"])
            self.assertEqual(root["progress"]["completed_block_indices"], list(range(10)))
            self.assertEqual(root["progress"]["completed_episode_count"], 490)
            self.assertEqual(root["progress"]["failure_stage"], "finalization")
            self.assertEqual(
                verified["attempt_status"], "incomplete_invalid_non_mergeable"
            )
            self.assertNotIn(
                b"secret finalizer details",
                b"".join(
                    path.read_bytes()
                    for path in store.run_root.rglob("*")
                    if path.is_file()
                ),
            )

    def test_verification_stage_failure_supersedes_but_retains_success_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            with patch.object(c4a.os, "fsync"):
                _populate_success_evidence(store, _schedule())
                store.finalize_success(_success_blocks())
                summary_bytes = store.success_summary_path.read_bytes()
                store.record_stage_failure(
                    failure_stage="verification",
                    error=RuntimeError("not persisted"),
                    completed_block_indices=list(range(10)),
                    completed_episode_count=490,
                )

            self.assertEqual(store.success_summary_path.read_bytes(), summary_bytes)
            self.assertEqual(
                c4a.verify_c4_artifacts(store.run_root)["attempt_status"],
                "incomplete_invalid_non_mergeable",
            )
    def test_verifier_rejects_noncanonical_or_corrupted_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = c4a.C4ArtifactStore.create(
                Path(temporary) / "runs", RUN_ID, _schedule(), _provenance(), ["runner"]
            )
            store.append_enqueue_event({"block_index": 0, "source_sequence": 0})
            with store.events_path.open("ab") as handle:
                handle.write(b'{"event_type":"publication"}\n')
            with self.assertRaisesRegex(c4a.NativeCharacterizationC4ArtifactError, "event_seal_invalid"):
                c4a.verify_c4_artifacts(store.run_root)


if __name__ == "__main__":
    import unittest

    unittest.main()
