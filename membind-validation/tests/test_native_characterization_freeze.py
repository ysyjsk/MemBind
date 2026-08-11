"""Offline contracts for the deterministic Native characterization freeze.

The fixture may hash semantic input from the four frozen calibration histories,
but it must never retain that input or inspect held-out semantic fields.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

from native_characterization_freeze import (  # noqa: E402
    build_artifacts,
    canonical_bytes,
    validate_artifact,
    write_artifacts,
)


WORKPLAN = REPO / "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
SPLIT = ROOT / "artifacts" / "dataset" / "frozen_split_v1_3.json"
SOURCE = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
    "longmemeval_s_cleaned.json"
)
CALIBRATION_IDS = ["07741c45", "b6019101", "6071bd76", "a2f3aa27"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class NativeCharacterizationFreezeTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.freeze, cls.phase_map = build_artifacts(
            repo_root=REPO,
            validation_root=ROOT,
            source_path=SOURCE,
        )
        cls.split = json.loads(SPLIT.read_text(encoding="utf-8"))

    def test_verified_inputs_and_protocol_binding_are_explicit(self):
        freeze = self.freeze
        self.assertEqual(freeze["schema_version"], "membind.native-characterization-freeze.v1")
        self.assertEqual(freeze["protocol"]["id"], "native-characterization-v1.1")
        self.assertEqual(freeze["protocol"]["workplan_sha256"], _sha256(WORKPLAN))
        self.assertEqual(freeze["dataset"]["split_sha256"], _sha256(SPLIT))
        self.assertEqual(freeze["dataset"]["source_sha256"], _sha256(SOURCE))
        self.assertEqual(
            freeze["state_transition"]["source_state_sha256"],
            "fb57c0edb6388c2ae94c6ba338e1671c39fa08e218cfc96566ee4d315b2e231d",
        )
        self.assertEqual(
            freeze["state_transition"]["offline_transition_state_sha256"],
            "af7651fb8d5e5f6e4b6b43fe028969ce45182387326c162bcd8d45df0b47b731",
        )
        self.assertNotIn("current_state_sha256", freeze["input_hashes"])

    def test_only_calibration_histories_are_materialized_in_frozen_order(self):
        histories = self.freeze["dataset"]["calibration_histories"]
        self.assertEqual([item["history_id"] for item in histories], CALIBRATION_IDS)
        self.assertEqual([item["episode_count"] for item in histories], [49, 49, 46, 44])
        for history in histories:
            episodes = history["episodes"]
            self.assertEqual(
                [item["source_sequence"] for item in episodes],
                list(range(history["episode_count"])),
            )
            for episode in episodes:
                self.assertRegex(episode["episode_source_sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(episode["prefix_sha256"], r"^[0-9a-f]{64}$")

    def test_u0_is_not_silently_stabilized_or_cached(self):
        objects = self.freeze["objects"]
        self.assertEqual(objects["primary"]["id"], "U0")
        self.assertEqual(objects["guardrail"]["id"], "U0-S")
        self.assertFalse(objects["primary"]["policies"]["deterministic_candidate_ordering"])
        self.assertFalse(objects["primary"]["policies"]["prompt_cache"])
        self.assertFalse(objects["primary"]["policies"]["embedding_cache"])
        self.assertFalse(objects["primary"]["policies"]["caching_counting_embedder"])
        self.assertEqual(objects["primary"]["policies"]["cross_run_cache_carry_over"], "prohibited")
        self.assertTrue(objects["guardrail"]["policies"]["deterministic_candidate_ordering"])
        self.assertEqual(objects["guardrail"]["role"], "separately_labeled_guardrail_not_primary")
        self.assertEqual(
            self.freeze["input_hashes"]["u0_runtime_source_sha256"],
            _sha256(ROOT / "src/native_characterization_runtime.py"),
        )
        self.assertEqual(
            self.freeze["input_hashes"]["c0_runner_source_sha256"],
            _sha256(ROOT / "src/native_characterization_c0.py"),
        )

    def test_runtime_identities_and_compatibility_policy_are_frozen(self):
        identities = self.freeze["runtime_identities"]
        self.assertEqual(identities["graphiti"]["version"], "0.29.3")
        self.assertEqual(
            identities["graphiti"]["commit"],
            "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        )
        self.assertEqual(identities["construction"]["vllm_version"], "0.26.0")
        self.assertEqual(identities["construction"]["max_model_len"], 40960)
        self.assertEqual(identities["construction"]["model_revision"], "6e2312b85c2ae9a31f629f24493b79d8b02eab1a")
        self.assertEqual(identities["embedding"]["dimension"], 1024)
        self.assertEqual(identities["embedding"]["pooling"], "last_token")
        self.assertEqual(identities["embedding"]["normalization"], "l2")
        policy = self.freeze["construction_compatibility_policy"]
        self.assertEqual(policy["classification"], "qwen_vllm_compatibility_adapter")
        self.assertFalse(policy["upstream_graphiti_behavior"])
        self.assertEqual(policy["requested_max_tokens"], 16384)
        self.assertEqual(policy["safety_margin_tokens"], 32)
        self.assertEqual(policy["episode_indices"], [0])

    def test_screening_order_is_bounded_and_deterministic(self):
        screening = self.freeze["screening"]
        self.assertEqual(screening["c0"]["history_id"], CALIBRATION_IDS[0])
        self.assertEqual(screening["c0"]["source_sequence"], 0)
        self.assertEqual(
            [block["history_id"] for block in screening["e1_e2"]["block_order"]],
            CALIBRATION_IDS,
        )
        self.assertTrue(screening["e1_e2"]["shared_native_trace"])
        self.assertEqual(screening["e3"]["history_id"], CALIBRATION_IDS[0])
        self.assertEqual(
            screening["e3"]["normalized_offered_load_order"],
            [0.5, 0.8, 1.0, 1.2, 1.5],
        )
        self.assertEqual(len(screening["e3"]["block_order"]), 10)
        self.assertNotIn("interarrival_seconds", screening["e3"])
        self.assertEqual(screening["e4"]["concurrency_order"], [1, 2, 4, 8])
        self.assertEqual(len(screening["e4"]["block_order"]), 4)

    def test_every_live_block_has_a_fresh_content_addressed_graph_namespace(self):
        screening = self.freeze["screening"]
        namespaces = [screening["c0"]["graph_namespace"]]
        for section in ("e1_e2", "e3", "e4"):
            namespaces.extend(
                block["graph_namespace"]
                for block in screening[section]["block_order"]
            )
        self.assertEqual(len(namespaces), 19)
        self.assertEqual(len(set(namespaces)), 19)
        for namespace in namespaces:
            self.assertRegex(namespace, r"^nc-(?:c0|e1e2|e3|e4)-[0-9a-f]{16}$")

    def test_phase_map_matches_the_instrumented_pinned_alias_boundaries(self):
        phases = self.phase_map["phases"]
        self.assertEqual(
            [(item["attribute"], item["phase"]) for item in phases],
            [
                ("add_episode", "add-episode"),
                ("retrieve_episodes", "previous-context"),
                ("extract_nodes", "node-extraction"),
                ("resolve_extracted_nodes", "node-resolution"),
                ("extract_edges", "edge-extraction"),
                ("resolve_extracted_edges", "edge-resolution"),
                ("extract_attributes_from_nodes", "attributes-summary"),
                ("_process_episode_data", "publication"),
            ],
        )
        self.assertTrue(all(item["dependency_class"] == "unclassified" for item in phases))

    def test_payload_hashes_validate_and_detect_mutation(self):
        validate_artifact(self.freeze)
        validate_artifact(self.phase_map)
        mutated = copy.deepcopy(self.freeze)
        mutated["screening"]["e4"]["concurrency_order"] = [1, 2]
        with self.assertRaisesRegex(ValueError, "payload_sha256"):
            validate_artifact(mutated)

    def test_artifacts_are_ascii_deterministic_and_written_atomically(self):
        rebuilt = build_artifacts(
            repo_root=REPO,
            validation_root=ROOT,
            source_path=SOURCE,
        )
        self.assertEqual(rebuilt, (self.freeze, self.phase_map))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            written = write_artifacts(self.freeze, self.phase_map, output)
            self.assertEqual(set(written), {"freeze.json", "phase_map.json"})
            for name, payload in (("freeze.json", self.freeze), ("phase_map.json", self.phase_map)):
                raw = (output / name).read_bytes()
                raw.decode("ascii")
                self.assertEqual(raw, canonical_bytes(payload) + b"\n")

    def test_no_held_out_ids_or_semantic_content_or_secret_fields_are_persisted(self):
        serialized = canonical_bytes({"freeze": self.freeze, "phase_map": self.phase_map}).decode("ascii")
        for evaluation_id in self.split["evaluation_question_ids"]:
            self.assertNotIn(evaluation_id, serialized)
        for development_id in self.split["compatibility_development_question_ids"]:
            self.assertNotIn(development_id, serialized)
        lowered = serialized.lower()
        for forbidden in (
            '"answer"',
            '"body"',
            '"content"',
            '"prompt"',
            '"response"',
            '"query"',
            '"parameters"',
            '"session_id"',
            '"api_key"',
            '"authorization"',
            '"created_at"',
            '"checked_at"',
            '"timestamp"',
        ):
            self.assertNotIn(forbidden, lowered)

    def test_input_hash_mismatch_and_missing_calibration_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_split = Path(tmp) / "split.json"
            payload = copy.deepcopy(self.split)
            payload["source_sha256"] = "0" * 64
            bad_split.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source hash"):
                build_artifacts(
                    repo_root=REPO,
                    validation_root=ROOT,
                    source_path=SOURCE,
                    split_path=bad_split,
                )

            payload = copy.deepcopy(self.split)
            payload["calibration_question_ids"] = ["missing"]
            bad_split.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "calibration"):
                build_artifacts(
                    repo_root=REPO,
                    validation_root=ROOT,
                    source_path=SOURCE,
                    split_path=bad_split,
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
