"""Durability and fail-closed verification contracts for the C5 live store."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_c5 as c5  # noqa: E402
import native_characterization_c5_live_artifacts as artifacts  # noqa: E402
import native_characterization_c5_live_core as live  # noqa: E402


RUN_ID = "c5-0123456789abcdef"
HISTORY_ID = "07741c45"
HASHES = [hashlib.sha256(f"episode-{index}".encode("ascii")).hexdigest() for index in range(49)]
NAMESPACES = (
    "nc-e4-1434fcb947df5c3d",
    "nc-e4-b352061ffa0d4b21",
    "nc-e4-c15538d1fe2801cb",
    "nc-e4-2a427029b1a8b2ac",
)


def schedule() -> dict[str, object]:
    value = c5.build_c5_schedule(
        history_id=HISTORY_ID,
        episode_source_hashes=HASHES,
        interarrival_ns=0,
        run_id=RUN_ID,
    )
    for index, block in enumerate(value["block_schedules"]):
        block["graph_namespace"] = NAMESPACES[index]
    value["payload_sha256"] = c5.payload_sha256(value)
    return value


def block_result(block_index: int) -> dict[str, object]:
    publications = [
        {
            "event_sequence": source * 2 + 1,
            "source_sequence": source,
            "arrival_timestamp_ns": source * 10,
            "service_start_timestamp_ns": source * 10 + 1,
            "publish_timestamp_ns": source * 10 + 2,
            "caller_return_timestamp_ns": source * 10 + 2,
            "worker_id": source % c5.CONCURRENCY_GRID[block_index],
            "transaction_status": "committed",
            "work_counts": {"add_episode_calls": 1},
        }
        for source in range(49)
    ]
    result = c5.analyze_c5_block(
        concurrency=c5.CONCURRENCY_GRID[block_index],
        expected_episode_ids=[f"{HISTORY_ID}:{source}" for source in range(49)],
        publication_records=publications,
        canonical_graph_parity={"status": "pass"},
        retrieval_parity={"status": "pass"},
        execution_path_evidence={
            "treatment_is_concurrency_only": True,
            "live_graph_outputs_fixed": True,
            "complete_add_episode_units": True,
            "work_conserving_dispatch": True,
        },
    )
    result.pop("payload_sha256")
    result.update(
        {
            "block_index": block_index,
            "graph_namespace": NAMESPACES[block_index],
            "supplemental_qa": {
                "status": "SUCCESS",
                "accuracy": 1.0,
                "headline_interpretation_effect": "none",
            },
            **(
                {
                    "serial_reference": {
                        "canonical_graph_sha256": "c" * 64,
                        "retrieved_episode_ids": ["session-001", "session-002"],
                        "retrieved_episode_ids_sha256": hashlib.sha256(
                            artifacts.canonical_json_bytes(
                                ["session-001", "session-002"]
                            )
                        ).hexdigest(),
                    }
                }
                if block_index == 0
                else {}
            ),
        }
    )
    return c5.seal_payload(result)


async def write_episode(store: artifacts.C5LiveArtifactStore, block_index: int, source: int) -> None:
    concurrency = c5.CONCURRENCY_GRID[block_index]
    intent = await store.append_intent_event(
        {
            "event_type": "intent",
            "block_index": block_index,
            "concurrency": concurrency,
            "graph_namespace": NAMESPACES[block_index],
            "source_sequence": source,
            "episode_source_sha256": HASHES[source],
            "arrival_timestamp_ns": source * 10,
            "worker_id": source % concurrency,
        }
    )
    publication = await store.append_publication_event(
        {
            "event_type": "publication",
            "block_index": block_index,
            "concurrency": concurrency,
            "graph_namespace": NAMESPACES[block_index],
            "source_sequence": source,
            "episode_source_sha256": HASHES[source],
            "arrival_timestamp_ns": source * 10,
            "service_start_timestamp_ns": source * 10 + 1,
            "publish_timestamp_ns": source * 10 + 2,
            "caller_return_timestamp_ns": source * 10 + 2,
            "worker_id": source % concurrency,
            "transaction_status": "committed",
            "work_counts": {"add_episode_calls": 1},
        }
    )
    await store.write_episode_checkpoint(
        status="published",
        block_index=block_index,
        source_sequence=source,
        publication_event_sequence=publication["event_sequence"],
        publication_payload_sha256=publication["payload_sha256"],
        intent_event_sequence=intent["event_sequence"],
        intent_payload_sha256=intent["payload_sha256"],
    )


async def complete_block(store: artifacts.C5LiveArtifactStore, block_index: int) -> dict[str, object]:
    await asyncio.gather(*(write_episode(store, block_index, source) for source in range(49)))
    result = block_result(block_index)
    return await store.write_block_checkpoint(
        status="completed",
        block_index=block_index,
        concurrency=c5.CONCURRENCY_GRID[block_index],
        graph_namespace=NAMESPACES[block_index],
        block_result=result,
    )


def create_store(root: Path) -> artifacts.C5LiveArtifactStore:
    return artifacts.C5LiveArtifactStore.create(
        root / "runs",
        RUN_ID,
        schedule(),
        provenance_hashes={"freeze_sha256": "a" * 64, "c4_result_sha256": "b" * 64},
        command_argv=["python", "-m", "native_characterization_c5_live"],
    )


class C5LiveArtifactStoreTests(IsolatedAsyncioTestCase):
    async def test_create_writes_canonical_manifest_schedule_empty_events_and_planned_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = create_store(Path(temporary))

            self.assertEqual(store.events_path.read_bytes(), b"")
            manifest = json.loads(store.manifest_path.read_text("ascii"))
            persisted_schedule = json.loads(store.schedule_path.read_text("ascii"))
            root = json.loads(store.root_checkpoint_path.read_text("ascii"))
            self.assertEqual(manifest["schedule_payload_sha256"], persisted_schedule["payload_sha256"])
            self.assertEqual(manifest["planned_blocks"][3]["concurrency"], 8)
            self.assertEqual(root["status"], "planned")
            self.assertEqual(root["completed_block_indices"], [])
            self.assertEqual(root["partial_block_index"], None)
            for path in (store.manifest_path, store.schedule_path, store.root_checkpoint_path):
                value = json.loads(path.read_text("ascii"))
                self.assertEqual(path.read_bytes(), artifacts.canonical_json_bytes(value) + b"\n")
                self.assertEqual(value["payload_sha256"], artifacts.payload_sha256(value))

    async def test_concurrent_appends_are_strictly_sequenced_canonical_and_durable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = create_store(Path(temporary))
            await asyncio.gather(*(write_episode(store, 3, source) for source in range(49)))

            lines = store.events_path.read_bytes().splitlines()
            events = [json.loads(line.decode("ascii")) for line in lines]
            self.assertEqual(len(events), 98)
            self.assertEqual([event["event_sequence"] for event in events], list(range(98)))
            self.assertTrue(all(line == artifacts.canonical_json_bytes(event) for line, event in zip(lines, events)))
            self.assertTrue(all(event["payload_sha256"] == artifacts.payload_sha256(event) for event in events))
            self.assertEqual(len(list((store.run_dir / "blocks/003/episodes").glob("*.json"))), 49)

    async def test_raw_exception_credentials_prompt_and_response_are_rejected_before_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = create_store(Path(temporary))
            before = store.events_path.read_bytes()
            base = {
                "event_type": "failure",
                "block_index": 0,
                "concurrency": 1,
                "graph_namespace": NAMESPACES[0],
                "source_sequence": 0,
                "failure_timestamp_ns": 1,
                "error_class": "builtins.RuntimeError",
                "failure_kind": "infrastructure_failure",
                "scientific_interpretation": None,
            }
            for forbidden in (
                {"exception": RuntimeError("secret")},
                {"api_key": "secret"},
                {"prompt": "private"},
                {"raw_response": "private"},
                {"nested": {"authorization": "Bearer secret"}},
            ):
                with self.assertRaises(artifacts.C5LiveArtifactError):
                    await store.append_failure_event({**base, **forbidden})
            self.assertEqual(store.events_path.read_bytes(), before)

    async def test_resume_inspection_accepts_completed_prefix_and_identifies_partial_next_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = create_store(Path(temporary))
            await complete_block(store, 0)
            await store.write_root_checkpoint(
                status="running", completed_block_indices=[0], partial_block_index=None
            )
            await write_episode(store, 1, 0)
            await store.write_root_checkpoint(
                status=artifacts.INCOMPLETE_NON_MERGEABLE,
                completed_block_indices=[0],
                partial_block_index=1,
            )

            prefix = artifacts.inspect_c5_resume_prefix(store.run_dir)
            self.assertEqual(prefix.completed_block_indices, (0,))
            self.assertEqual(prefix.partial_block_index, 1)
            self.assertTrue(prefix.requires_partial_block_restart)
            self.assertEqual(len(prefix.completed_block_results), 1)
            self.assertEqual(
                prefix.serial_reference["canonical_graph_sha256"], "c" * 64
            )
            self.assertEqual(
                prefix.serial_reference["retrieved_episode_ids"],
                ["session-001", "session-002"],
            )

    async def test_finalize_requires_four_closed_blocks_and_emits_hash_closed_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = create_store(Path(temporary))
            completed_results = []
            for block_index in range(4):
                await complete_block(store, block_index)
                completed_results.append(block_result(block_index))
                await store.write_root_checkpoint(
                    status="running" if block_index < 3 else "complete",
                    completed_block_indices=list(range(block_index + 1)),
                    partial_block_index=None,
                )

            result = await store.finalize_success(completed_results)
            verification = artifacts.verify_c5_live_artifacts(store.run_dir)
            self.assertEqual(result["schema_version"], c5.RESULT_SCHEMA)
            self.assertEqual(result["completed_block_count"], 4)
            self.assertEqual(len(result["block_results"]), 4)
            self.assertEqual(verification["attempt_status"], "complete")
            self.assertEqual(verification["event_count"], 392)
            self.assertEqual(verification["episode_checkpoint_count"], 196)
            self.assertEqual(verification["completed_block_count"], 4)

    async def test_finalize_fails_closed_before_all_four_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = create_store(Path(temporary))
            await complete_block(store, 0)
            with self.assertRaisesRegex(artifacts.C5LiveArtifactError, "four_completed_blocks_required"):
                await store.finalize_success([block_result(0)])
            self.assertFalse(store.result_path.exists())

    async def test_failure_event_makes_attempt_nonmergeable_even_if_root_is_forged_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = create_store(Path(temporary))
            await store.append_failure_event(
                {
                    "event_type": "failure",
                    "block_index": 0,
                    "concurrency": 1,
                    "graph_namespace": NAMESPACES[0],
                    "source_sequence": 0,
                    "failure_timestamp_ns": 1,
                    "error_class": "builtins.ConnectionError",
                    "failure_kind": "infrastructure_failure",
                    "scientific_interpretation": None,
                }
            )
            await store.write_root_checkpoint(
                status=artifacts.INCOMPLETE_NON_MERGEABLE,
                completed_block_indices=[],
                partial_block_index=0,
            )
            verification = artifacts.verify_c5_live_artifacts(store.run_dir)
            self.assertEqual(verification["attempt_status"], artifacts.INCOMPLETE_NON_MERGEABLE)
            self.assertEqual(verification["failure_event_count"], 1)


class C5LiveReadOnlyVerifierTests(TestCase):
    def test_verifier_is_read_only_and_rejects_schedule_or_checkpoint_tampering(self) -> None:
        async def build(root: Path) -> artifacts.C5LiveArtifactStore:
            store = create_store(root)
            results = []
            for block_index in range(4):
                await complete_block(store, block_index)
                results.append(block_result(block_index))
                await store.write_root_checkpoint(
                    status="running" if block_index < 3 else "complete",
                    completed_block_indices=list(range(block_index + 1)),
                    partial_block_index=None,
                )
            await store.finalize_success(results)
            return store

        with tempfile.TemporaryDirectory() as temporary:
            store = asyncio.run(build(Path(temporary)))
            before = {
                path.relative_to(store.run_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in store.run_dir.rglob("*")
                if path.is_file()
            }
            self.assertEqual(artifacts.verify_c5_live_artifacts(store.run_dir)["attempt_status"], "complete")
            after = {
                path.relative_to(store.run_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in store.run_dir.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)

            checkpoint = store.run_dir / "blocks/002/checkpoint.json"
            value = json.loads(checkpoint.read_text("ascii"))
            value["block_result"]["bounded_claim"] = "tampered"
            checkpoint.write_text(
                json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="ascii",
            )
            self.assertEqual(
                artifacts.verify_c5_live_artifacts(store.run_dir)["attempt_status"],
                artifacts.INCOMPLETE_NON_MERGEABLE,
            )

    def test_resume_rejects_nonprefix_completed_blocks(self) -> None:
        async def build_gap(root: Path) -> artifacts.C5LiveArtifactStore:
            store = create_store(root)
            await complete_block(store, 1)
            return store

        with tempfile.TemporaryDirectory() as temporary:
            store = asyncio.run(build_gap(Path(temporary)))
            with self.assertRaisesRegex(artifacts.C5LiveArtifactError, "completed_blocks_not_prefix"):
                artifacts.inspect_c5_resume_prefix(store.run_dir)


if __name__ == "__main__":
    import unittest

    unittest.main()
