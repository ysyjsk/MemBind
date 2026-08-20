from __future__ import annotations

import unittest
from pathlib import Path

from analyze import build_analysis, load_frozen_evidence


ROOT = Path(__file__).resolve().parents[2]
VALIDATION = (
    ROOT
    / "siliconflow_judge_validation_20260819"
    / "artifacts"
    / "siliconflow-validation-20260819-002"
)


class FrozenEvidenceIntegrationTests(unittest.TestCase):
    def test_exact_frozen_evidence_reduces_without_runtime_calls(self) -> None:
        rows, judges, sources = load_frozen_evidence(
            VALIDATION / "INPUT_MANIFEST.json", VALIDATION / "RESULTS.json"
        )
        result = build_analysis(rows, judges)

        self.assertEqual(len(sources["read_only_sources"]), 8)
        self.assertFalse(sources["historical_artifacts_modified"])
        self.assertEqual(result["construction_calls"], 0)
        self.assertEqual(result["reader_calls"], 0)
        self.assertEqual(result["methods"]["U0"]["accuracy"], 0.5)
        self.assertEqual(result["methods"]["P(C=2)"]["accuracy"], 0.5)
        self.assertEqual(result["paired"]["discordant_count"], 0)
        self.assertEqual(result["judge_validation"]["invalid_count"], 0)
        self.assertEqual(result["judge_validation"]["agreement_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
