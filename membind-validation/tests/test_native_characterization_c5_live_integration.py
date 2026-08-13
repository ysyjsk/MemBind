"""Integrated mock TDD for C5 core plus the real fsync artifact store."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import dataset  # noqa: E402
import native_characterization_c5 as c5  # noqa: E402
import native_characterization_c5_live_artifacts as artifacts  # noqa: E402
import native_characterization_c5_live_core as core  # noqa: E402


RUN_ID = "c5-0123456789abcdef"
HASHES = [hashlib.sha256(f"episode-{index}".encode()).hexdigest() for index in range(49)]


def schedule() -> dict[str, object]:
    freeze = json.loads(
        (ROOT / "artifacts/native_characterization/freeze_reference_aligned_64k.json")
        .read_text("ascii")
    )
    return core.load_frozen_e4_schedule(
        freeze, run_id=RUN_ID, episode_source_hashes=HASHES
    )


def episodes() -> list[c5.Episode]:
    return [
        c5.Episode(
            index,
            dataset.Episode(
                question_id=core.FROZEN_HISTORY_ID,
                group_id=core.FROZEN_HISTORY_ID,
                session_id=f"session-{index:03d}",
                source_sequence=index,
                source_hash=HASHES[index],
                reference_time="2026-08-01T00:00:00+00:00",
                body=f"offline fixture {index}",
            ),
        )
        for index in range(49)
    ]


def graph(namespace: str) -> dict[str, object]:
    return {
        "entities": [
            {
                "group_id": namespace,
                "name": "alpha",
                "labels": ["Entity"],
                "summary": "stable",
                "attributes": {},
            }
        ],
        "edges": [],
        "episodes": [
            {
                "source_sequence": index,
                "source_hash": HASHES[index],
                "session_id": f"session-{index:03d}",
            }
            for index in range(49)
        ],
    }


class Runtime:
    def __init__(self, block: core.C5Block, *, fail_source: int | None = None) -> None:
        self.block = block
        self.fail_source = fail_source
        self.counts = core.NamespaceCounts(0, 0)

    async def namespace_counts(self) -> core.NamespaceCounts:
        return self.counts

    async def clear_namespace(self) -> None:
        self.counts = core.NamespaceCounts(0, 0)

    async def add_episode(self, episode: c5.Episode) -> dict[str, object]:
        await asyncio.sleep(0)
        if episode.source_sequence == self.fail_source:
            raise ConnectionError("private endpoint disconnected")
        return {"work_counts": {"add_episode_calls": 1}}

    async def export_canonical_graph(self) -> dict[str, object]:
        return graph(self.block.graph_namespace)

    async def evaluate_retrieval(
        self, reference_episode_ids: list[str] | None
    ) -> dict[str, object]:
        ids = ["session-001", "session-002"]
        return {
            "retrieved_episode_ids": ids,
            "metrics": {
                "evidence_recall_at_5": 1.0,
                "evidence_recall_at_10": 1.0,
                "episode_set_overlap_with_m0": 1.0,
                "rank_biased_overlap_with_m0": 1.0,
            },
            "results": [{"rank": 1, "fact": "private fact must not persist"}],
        }

    async def close(self) -> None:
        return None


async def qa(_runtime: Runtime, _block: core.C5Block) -> dict[str, object]:
    return {"status": "SUCCESS", "correct": True}


class C5LiveIntegratedMockTests(IsolatedAsyncioTestCase):
    def store(self, root: Path) -> artifacts.C5LiveArtifactStore:
        return artifacts.C5LiveArtifactStore.create(
            root,
            RUN_ID,
            schedule(),
            {"freeze_sha256": "a" * 64, "c4_summary_sha256": "b" * 64},
            ["native-characterization-c5-live", "--run-id", RUN_ID],
        )

    async def test_success_is_hash_closed_and_persists_no_graph_fact_or_answer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(Path(temporary))

            async def factory(block: core.C5Block) -> Runtime:
                return Runtime(block)

            result = await core.run_c5_live_core(
                schedule=schedule(),
                episodes=episodes(),
                episode_source_hashes=HASHES,
                runtime_factory=factory,
                store=store,
                now_ns=core.MonotonicCounter(),
                qa_evaluator=qa,
            )
            verification = artifacts.verify_c5_live_artifacts(store.run_dir)
            raw = b"\n".join(
                path.read_bytes()
                for path in store.run_dir.rglob("*")
                if path.is_file()
            )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(verification["attempt_status"], "complete")
        self.assertEqual(verification["event_count"], 392)
        self.assertNotIn(b"private fact", raw)
        self.assertNotIn(b"offline fixture", raw)
        self.assertNotIn(b'"entities"', raw)

    async def test_failure_is_nonmergeable_and_resume_keeps_closed_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(Path(temporary))

            async def failing_factory(block: core.C5Block) -> Runtime:
                return Runtime(block, fail_source=0 if block.block_index == 1 else None)

            failed = await core.run_c5_live_core(
                schedule=schedule(),
                episodes=episodes(),
                episode_source_hashes=HASHES,
                runtime_factory=failing_factory,
                store=store,
                now_ns=core.MonotonicCounter(),
                qa_evaluator=qa,
            )
            self.assertEqual(failed["completed_block_indices"], [0])
            self.assertEqual(
                artifacts.verify_c5_live_artifacts(store.run_dir)["attempt_status"],
                artifacts.INCOMPLETE_NON_MERGEABLE,
            )
            recovery = artifacts.recover_c5_terminal_failure_to_resume_prefix(
                run_dir=store.run_dir,
                schedule=schedule(),
                provenance_hashes={
                    "freeze_sha256": "a" * 64,
                    "c4_summary_sha256": "b" * 64,
                },
            )
            self.assertEqual(recovery["completed_block_indices"], [0])
            self.assertEqual(recovery["next_block_index"], 1)
            self.assertTrue(recovery["recovered_terminal_failure"])
            self.assertTrue((store.run_dir / "resume_rollback_audit.json").is_file())
            inspection = artifacts.inspect_c5_resume_prefix(store.run_dir)
            reference = core.serial_reference_from_artifact(inspection.serial_reference)
            prefix = core.C5ResumePrefix(
                completed_block_indices=inspection.completed_block_indices,
                partial_block_index=inspection.partial_block_index,
                serial_reference=reference,
                completed_block_results=inspection.completed_block_results,
            )

            store.close()
            resumed_store = artifacts.C5LiveArtifactStore.open_existing(store.run_dir)
            async def healthy_factory(block: core.C5Block) -> Runtime:
                runtime = Runtime(block)
                if block.block_index == 1:
                    runtime.counts = core.NamespaceCounts(3, 2)
                return runtime

            resumed = await core.run_c5_live_core(
                schedule=schedule(),
                episodes=episodes(),
                episode_source_hashes=HASHES,
                runtime_factory=healthy_factory,
                store=resumed_store,
                now_ns=core.MonotonicCounter(),
                resume_prefix=prefix,
                qa_evaluator=qa,
            )
            verification = artifacts.verify_c5_live_artifacts(store.run_dir)

        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed["completed_block_indices"], [0, 1, 2, 3])
        self.assertEqual(verification["attempt_status"], "complete")
        self.assertEqual(verification["event_count"], 392)
        self.assertEqual(verification["failure_event_count"], 0)

    async def test_repeated_recovery_preserves_the_prior_terminal_failure_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self.store(Path(temporary))

            async def failing_factory(block: core.C5Block) -> Runtime:
                return Runtime(block, fail_source=0)

            await core.run_c5_live_core(
                schedule=schedule(),
                episodes=episodes(),
                episode_source_hashes=HASHES,
                runtime_factory=failing_factory,
                store=store,
                now_ns=core.MonotonicCounter(),
                qa_evaluator=qa,
            )
            store.close()
            first = artifacts.recover_c5_terminal_failure_to_resume_prefix(
                run_dir=store.run_dir,
                schedule=schedule(),
                provenance_hashes={
                    "freeze_sha256": "a" * 64,
                    "c4_summary_sha256": "b" * 64,
                },
            )
            first_audit = json.loads(
                (store.run_dir / "resume_rollback_audits/000000.json").read_text(
                    "ascii"
                )
            )
            second = artifacts.prepare_c5_running_resume_prefix(
                run_dir=store.run_dir,
                schedule=schedule(),
                provenance_hashes={
                    "freeze_sha256": "a" * 64,
                    "c4_summary_sha256": "b" * 64,
                },
            )
            second_audit = json.loads(
                (store.run_dir / "resume_rollback_audits/000001.json").read_text(
                    "ascii"
                )
            )

        self.assertEqual(
            first_audit["payload_sha256"], first["rollback_audit_payload_sha256"]
        )
        self.assertEqual(
            second_audit["previous_recovery_audit_payload_sha256"],
            first_audit["payload_sha256"],
        )
        self.assertEqual(
            second["rollback_audit_payload_sha256"], second_audit["payload_sha256"]
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
