"""Frozen semantic contracts for the public 14-item Judge qualification set."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.judge_qualification import (  # noqa: E402
    JUDGE_QUALIFICATION_ONLY,
    build_judge_qualification_freeze,
    build_strict_judge_qualification_freeze,
    canonical_json_bytes,
    validate_judge_qualification_freeze,
    validate_strict_judge_qualification_freeze,
)


FIXTURE = ROOT / "fixtures/judge_qualification_14_v1.json"
OFFLINE_MANIFEST = ROOT / "artifacts/protocol/judge_upstream_manifest_20260812.json"
QUALIFICATION_SOURCE = ROOT / "src/evaluation/judge_qualification.py"
LIVE_SOURCE = ROOT / "src/evaluation/judge_qualification_live.py"
EXPECTED_ITEM_LABELS = tuple(
    (f"qualification-{route}-{suffix}", label)
    for route in (
        "single-session-user",
        "single-session-assistant",
        "multi-session",
        "temporal-reasoning",
        "knowledge-update",
        "single-session-preference",
        "abstention",
    )
    for suffix, label in (("yes", True), ("no", False))
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class JudgeQualificationProductionFixtureTests(TestCase):
    maxDiff = None

    def test_fixture_is_canonical_public_semantic_and_balanced(self) -> None:
        raw = FIXTURE.read_bytes()
        fixture = json.loads(raw.decode("ascii"))
        self.assertEqual(raw, canonical_json_bytes(fixture) + b"\n")
        self.assertEqual(fixture["scientific_surface"], JUDGE_QUALIFICATION_ONLY)
        self.assertEqual(
            tuple((item["item_id"], item["human_label"]) for item in fixture["items"]),
            EXPECTED_ITEM_LABELS,
        )
        self.assertEqual(sum(item["human_label"] for item in fixture["items"]), 7)
        rendered = raw.decode("ascii")
        self.assertNotIn("Frozen question", rendered)
        self.assertNotIn("frozen-candidate", rendered)
        self.assertNotIn("PRIVATE", rendered)

        by_id = {item["item_id"]: item for item in fixture["items"]}
        self.assertIn("now works at OpenAI", by_id["qualification-knowledge-update-yes"]["hypothesis"])
        self.assertEqual(by_id["qualification-knowledge-update-no"]["hypothesis"], "Ravi works at Google.")
        self.assertIn("cannot determine", by_id["qualification-abstention-yes"]["hypothesis"])
        self.assertEqual(by_id["qualification-abstention-no"]["hypothesis"], "Noor's first bicycle was red.")
        self.assertIn("vegetarian", by_id["qualification-single-session-preference-yes"]["hypothesis"])
        self.assertIn("steakhouse", by_id["qualification-single-session-preference-no"]["hypothesis"])

    def test_production_freeze_rebuilds_from_real_content_bindings(self) -> None:
        freeze = build_judge_qualification_freeze(
            validation_root=ROOT,
            fixture_path=FIXTURE.relative_to(ROOT),
            fixture_sha256=_sha(FIXTURE),
            offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
            offline_manifest_sha256=_sha(OFFLINE_MANIFEST),
            qualification_source_path=QUALIFICATION_SOURCE.relative_to(ROOT),
            qualification_source_sha256=_sha(QUALIFICATION_SOURCE),
        )
        self.assertEqual(validate_judge_qualification_freeze(freeze, ROOT), freeze)
        self.assertEqual(len(freeze["items"]), 14)
        self.assertEqual(
            tuple((item["item_id"], item["human_label"]) for item in freeze["items"]),
            EXPECTED_ITEM_LABELS,
        )
        self.assertEqual(len({item["official_prompt_sha256"] for item in freeze["items"]}), 14)

    def test_strict_freeze_validates_upstream_manifest_and_binds_live_wire_source(self) -> None:
        freeze = build_strict_judge_qualification_freeze(
            validation_root=ROOT,
            fixture_path=FIXTURE.relative_to(ROOT),
            offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
            qualification_source_path=QUALIFICATION_SOURCE.relative_to(ROOT),
            qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
        )
        self.assertEqual(validate_strict_judge_qualification_freeze(freeze, ROOT), freeze)
        self.assertEqual(freeze["upstream_manifest_validation"], "strict_regeneration_match")
        self.assertEqual(
            set(freeze["bindings"]),
            {
                "offline_manifest",
                "qualification_fixture",
                "qualification_source",
                "qualification_live_source",
            },
        )

    def test_strict_freeze_rejects_a_canonical_but_fabricated_upstream_manifest(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for source in (FIXTURE, QUALIFICATION_SOURCE, LIVE_SOURCE):
                relative = source.relative_to(ROOT)
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            fake = root / OFFLINE_MANIFEST.relative_to(ROOT)
            fake.parent.mkdir(parents=True, exist_ok=True)
            fake.write_bytes(
                canonical_json_bytes(
                    {
                        "schema_version": "membind.judge-upstream-manifest.v1",
                        "status": "offline_implementation",
                        "payload_sha256": "a" * 64,
                    }
                )
                + b"\n"
            )
            with self.assertRaises((ValueError, RuntimeError)):
                build_strict_judge_qualification_freeze(
                    validation_root=root,
                    fixture_path=FIXTURE.relative_to(ROOT),
                    offline_manifest_path=OFFLINE_MANIFEST.relative_to(ROOT),
                    qualification_source_path=QUALIFICATION_SOURCE.relative_to(ROOT),
                    qualification_live_source_path=LIVE_SOURCE.relative_to(ROOT),
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
