"""Fail-closed C2 completion and immutable artifact-surface contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import native_characterization_c2 as c2  # noqa: E402
from native_characterization_instrumentation import PatchHandle  # noqa: E402
from native_characterization_tracing import SpanRecord  # noqa: E402
from tests import test_native_characterization_c2 as fixtures  # noqa: E402


def _no_measurement_installer(*_args, **_kwargs):
    return PatchHandle()


def _complete_measurement_installer(graphiti, recorder, **_kwargs):
    phase_module = _kwargs["phase_module"]
    original = phase_module.resolve_extracted_nodes

    async def measured(*args, **kwargs):
        with recorder.span(
            "candidate-embedding",
            operation_class="fixture",
            metadata={"text_count": 1},
        ):
            pass
        with recorder.span(
            "candidate-search",
            operation_class="fixture",
            metadata={"candidate_count": 2, "candidate_query_count": 1},
        ):
            pass
        with recorder.span(
            "invalidation-update",
            operation_class="fixture",
            metadata={
                "invalidation_candidate_count": 0,
                "invalidated_count": 0,
                "new_edge_expired_count": 0,
                "timing_scope": "fixture",
            },
        ):
            pass
        return await original(*args, **kwargs)

    phase_module.resolve_extracted_nodes = measured
    handle = PatchHandle()
    handle.add(lambda: setattr(phase_module, "resolve_extracted_nodes", original))
    return handle


async def _prefix(_driver, group_id):
    index = int(group_id.rsplit("-", 1)[-1], 16)
    return {
        "graph_prefix_node_count": index,
        "graph_prefix_relationship_count": index * 2,
    }


class NativeCharacterizationC2CompletionContractTests(TestCase):
    def test_each_hard_telemetry_field_fails_closed_independently(self):
        meta = {
            "episode_id": "history:0",
            "source_sequence": 0,
            "episode_source_sha256": "a" * 64,
            "prefix_sha256": "b" * 64,
        }

        def records():
            return [
                SpanRecord(0, "prefix", None, "run", "history:0", 0, "graph-prefix-snapshot", "snapshot", 1, 2, "ok", None, {"graph_prefix_node_count": 0, "graph_prefix_relationship_count": 0}),
                SpanRecord(1, "root", None, "run", "history:0", 0, "add-episode", None, 10, 200, "ok", None),
                SpanRecord(2, "context", "root", "run", "history:0", 0, "previous-context", None, 11, 15, "ok", None),
                SpanRecord(3, "node-extraction", "root", "run", "history:0", 0, "node-extraction", None, 16, 35, "ok", None),
                SpanRecord(4, "llm", "node-extraction", "run", "history:0", 0, "llm", "logical-call", 17, 30, "ok", None, {"prompt_name": "extract_nodes", "retry_count": 0, "input_tokens": 7, "output_tokens": 3}),
                SpanRecord(5, "transport", "llm", "run", "history:0", 0, "llm-transport", "request-attempt", 18, 29, "ok", None, {"attempt_index": 0, "input_tokens": 7, "output_tokens": 3}),
                SpanRecord(6, "node-resolution", "root", "run", "history:0", 0, "node-resolution", None, 36, 80, "ok", None),
                SpanRecord(7, "embedding", "node-resolution", "run", "history:0", 0, "embedding", "create_batch", 37, 45, "ok", None, {"text_count": 1, "dimension": 4}),
                SpanRecord(8, "candidate-embedding", "node-resolution", "run", "history:0", 0, "candidate-embedding", "node-dedup", 37, 45, "ok", None, {"text_count": 1}),
                SpanRecord(9, "candidate-search", "node-resolution", "run", "history:0", 0, "candidate-search", "node-dedup", 36, 70, "ok", None, {"candidate_count": 2, "candidate_query_count": 1}),
                SpanRecord(10, "database-query", "candidate-search", "run", "history:0", 0, "database", "query", 46, 60, "ok", None),
                SpanRecord(11, "edge-extraction", "root", "run", "history:0", 0, "edge-extraction", None, 81, 100, "ok", None),
                SpanRecord(12, "edge-resolution", "root", "run", "history:0", 0, "edge-resolution", None, 101, 140, "ok", None),
                SpanRecord(13, "invalidation", "edge-resolution", "run", "history:0", 0, "invalidation-update", "existing-edge-mutation", 120, 130, "ok", None, {"invalidation_candidate_count": 0, "invalidated_count": 0, "new_edge_expired_count": 0}),
                SpanRecord(14, "attributes", "root", "run", "history:0", 0, "attributes-summary", None, 141, 160, "ok", None),
                SpanRecord(15, "publication", "root", "run", "history:0", 0, "publication", None, 161, 200, "ok", None),
                SpanRecord(16, "transaction", "publication", "run", "history:0", 0, "database-transaction", "write", 165, 195, "ok", None, {"transaction_id": "tx-1"}),
                SpanRecord(17, "database-write", "transaction", "run", "history:0", 0, "database", "write", 170, 190, "ok", None, {"transaction_id": "tx-1"}),
            ]

        def remove_phase(value, phase):
            value[:] = [record for record in value if record.phase != phase]

        complete = c2._episode_analysis(records(), meta)
        self.assertEqual(complete["telemetry_completeness"]["status"], "complete")
        self.assertEqual(complete["work_volume"]["candidate_count"], 2)
        self.assertEqual(
            complete["graph_prefix_size"],
            {"node_count": 0, "relationship_count": 0},
        )

        mutations = {
            "phase_boundaries": lambda value: remove_phase(value, "node-extraction"),
            "llm_telemetry": lambda value: remove_phase(value, "llm"),
            "llm_transport": lambda value: remove_phase(value, "llm-transport"),
            "embedding_telemetry": lambda value: remove_phase(value, "embedding"),
            "database_telemetry": lambda value: remove_phase(value, "database"),
            "database_transaction": lambda value: remove_phase(value, "database-transaction"),
            "candidate_counts": lambda value: next(record for record in value if record.phase == "candidate-search").metadata.pop("candidate_count"),
            "candidate_embedding": lambda value: remove_phase(value, "candidate-embedding"),
            "candidate_search": lambda value: remove_phase(value, "candidate-search"),
            "invalidation_update": lambda value: remove_phase(value, "invalidation-update"),
            "graph_prefix_size": lambda value: remove_phase(value, "graph-prefix-snapshot"),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                value = records()
                mutate(value)
                analysis = c2._episode_analysis(value, meta)
                self.assertIn(
                    field,
                    analysis["telemetry_completeness"]["missing_required_fields"],
                )

    def test_incomplete_telemetry_cannot_create_completed_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            freeze = fixtures._write_freeze(root)
            run_id = "c2-offline-incomplete-contract"
            with self.assertRaisesRegex(
                c2.NativeCharacterizationC2Error,
                "measurement_contract_incomplete",
            ):
                asyncio.run(
                    c2.execute_c2(
                        validation_root=root,
                        freeze_path=freeze.relative_to(root).as_posix(),
                        run_id=run_id,
                        authorization_checker=lambda _action: None,
                        runtime_factory=fixtures._fake_runtime_factory,
                        measurement_installer=_no_measurement_installer,
                        graph_prefix_collector=_prefix,
                    )
                )

            run_root = (
                root
                / "artifacts"
                / "native_characterization"
                / "runs"
                / run_id
            )
            self.assertFalse((run_root / "manifest.json").exists())
            checkpoint = json.loads((run_root / "checkpoint.json").read_text("ascii"))
            self.assertEqual(checkpoint["status"], "error")
            self.assertEqual(
                checkpoint["error_code"],
                "native_characterization_c2.NativeCharacterizationC2Error",
            )

    def test_complete_run_writes_frozen_surface_hash_inventory_and_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            freeze = fixtures._write_freeze(root)
            run_id = "c2-offline-complete-contract"
            result = asyncio.run(
                c2.execute_c2(
                    validation_root=root,
                    freeze_path=freeze.relative_to(root).as_posix(),
                    run_id=run_id,
                    authorization_checker=lambda _action: None,
                    runtime_factory=fixtures._fake_runtime_factory,
                    measurement_installer=_complete_measurement_installer,
                    graph_prefix_collector=_prefix,
                )
            )
            self.assertEqual(result["status"], "completed")
            run_root = (
                root
                / "artifacts"
                / "native_characterization"
                / "runs"
                / run_id
            )
            required = {
                "spans.jsonl",
                "llm.jsonl",
                "embedding.jsonl",
                "db.jsonl",
                "events.jsonl",
                "errors.jsonl",
                "checkpoint.json",
                "e1_breakdown.json",
            }
            self.assertTrue(all((run_root / name).is_file() for name in required))
            manifest = json.loads((run_root / "manifest.json").read_text("ascii"))
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(
                manifest["top_level_e1_breakdown_sha256"],
                result["top_level_e1_breakdown_sha256"],
            )
            self.assertEqual(
                manifest["telemetry_completeness"]["status"], "complete"
            )
            provenance = manifest["provenance"]
            self.assertEqual(
                provenance["creation_command"],
                ".venv/bin/python src/native_characterization_c2.py --live "
                f"--run-id {run_id}",
            )
            self.assertEqual(provenance["freeze_sha256"], c2._sha256_file(freeze))
            self.assertIn("c2_runner_source_sha256", provenance)
            self.assertIn("measurement_adapter_source_sha256", provenance)
            inventory = manifest["artifact_sha256"]
            self.assertTrue(required <= set(inventory))
            for relative, digest in inventory.items():
                self.assertEqual(
                    digest,
                    hashlib.sha256((run_root / relative).read_bytes()).hexdigest(),
                )

            for name in required - {"checkpoint.json", "e1_breakdown.json"}:
                envelopes = [
                    json.loads(line)
                    for line in (run_root / name).read_text("ascii").splitlines()
                ]
                self.assertEqual(len(envelopes), 5)
                self.assertTrue(
                    all(item["schema_version"].endswith(".v1") for item in envelopes)
                )
                self.assertTrue(all(item["run_id"] == run_id for item in envelopes))
                self.assertTrue(
                    all("episode_source_sha256" in item for item in envelopes)
                )
                self.assertTrue(all("prefix_sha256" in item for item in envelopes))

            breakdown = json.loads((run_root / "e1_breakdown.json").read_text("ascii"))
            self.assertEqual(breakdown["telemetry_completeness"]["status"], "complete")
            for phase in (
                "candidate-embedding",
                "candidate-search",
                "invalidation-update",
            ):
                self.assertIn(phase, breakdown["aggregate_phase_occupancy"])
            self.assertEqual(
                breakdown["aggregate"]["work_volume"]["candidate_count"], 10
            )

    def test_top_level_breakdown_failure_cannot_leave_completed_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            freeze = fixtures._write_freeze(root)
            run_id = "c2-offline-top-level-write-failure"
            top_level = root / "artifacts/native_characterization/e1_breakdown.json"
            real_atomic_json = c2._atomic_json

            def fail_top_level(path, payload):
                if Path(path) == top_level:
                    raise OSError("synthetic_top_level_write_failure")
                return real_atomic_json(path, payload)

            with patch.object(c2, "_atomic_json", side_effect=fail_top_level):
                with self.assertRaisesRegex(OSError, "synthetic_top_level_write_failure"):
                    asyncio.run(
                        c2.execute_c2(
                            validation_root=root,
                            freeze_path=freeze.relative_to(root).as_posix(),
                            run_id=run_id,
                            authorization_checker=lambda _action: None,
                            runtime_factory=fixtures._fake_runtime_factory,
                            measurement_installer=_complete_measurement_installer,
                            graph_prefix_collector=_prefix,
                        )
                    )

            run_root = (
                root
                / "artifacts"
                / "native_characterization"
                / "runs"
                / run_id
            )
            self.assertFalse((run_root / "manifest.json").exists())
            checkpoint = json.loads((run_root / "checkpoint.json").read_text("ascii"))
            self.assertEqual(checkpoint["status"], "error")


if __name__ == "__main__":
    import unittest

    unittest.main()
