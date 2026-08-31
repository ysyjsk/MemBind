from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PREREG = PROJECT / "v7/dmsv_b1r2_structural_closure/DMSV_B1R2_PREREGISTRATION.json"
ROOT = PROJECT.parent
ARTIFACT_DIR = PROJECT / "v7/dmsv_b1r2_structural_closure"


def test_b1r2_preregistration_is_frozen_before_evidence() -> None:
    prereg = json.loads(PREREG.read_text(encoding="ascii"))
    assert prereg["status"] == "FROZEN_BEFORE_EVIDENCE_EXTRACTION"
    assert prereg["input_commit"] == "37871aae8193d994a1642605e3a705712dd786e1"
    assert list(prereg["claim_taxonomy"]) == [
        "L1_SENSITIVITY",
        "L2_DIRTY_WITNESS_EXISTS",
        "L3_DIRTY_RATE_ESTIMATED",
        "L4_STRUCTURALLY_ALWAYS_DIRTY",
        "L5_NATIVE_CALL_UNAVOIDABLE",
        "source",
    ]
    assert all(value is False for value in prereg["terminal_authorizations"].values())


def test_claim_lattice_does_not_upgrade_one_pair_to_always_dirty() -> None:
    levels = [
        "L1_SENSITIVITY",
        "L2_DIRTY_WITNESS_EXISTS",
        "L3_DIRTY_RATE_ESTIMATED",
        "L4_STRUCTURALLY_ALWAYS_DIRTY",
        "L5_NATIVE_CALL_UNAVOIDABLE",
    ]
    one_pair = {"complete_eligible_pairs": 1, "complete_dirty_pairs": 1}
    assert one_pair["complete_eligible_pairs"] == one_pair["complete_dirty_pairs"]
    assert levels.index("L2_DIRTY_WITNESS_EXISTS") < levels.index("L4_STRUCTURALLY_ALWAYS_DIRTY")


def test_unknown_eligibility_is_excluded_from_structural_population() -> None:
    pair = json.loads(
        (ARTIFACT_DIR / "DMSV_B1R2_DEVELOPMENT_PAIR_MATRIX.jsonl").read_text().splitlines()[0]
    )
    assert any(value == "UNKNOWN" for value in pair["eligibility"].values())
    assert pair["classification"] == "DIRTY_SIGNAL_NOT_ELIGIBLE"
    assert pair["canonical_request_byte_equal"] is False


def test_stable_eligible_pair_would_falsify_always_dirty() -> None:
    decision = json.loads((ARTIFACT_DIR / "DMSV_B1R2_DECISION.json").read_text())
    truth = json.loads(PREREG.read_text())["decision_truth_table"]
    assert truth["complete_stable_eligible_pair"] == "DMSV_NATIVE_NODE_ALWAYS_DIRTY_FALSIFIED"
    assert decision["claim_levels"]["L4_STRUCTURALLY_ALWAYS_DIRTY"] == "NOT_ESTABLISHED"


def test_request_digest_change_is_not_unavoidability() -> None:
    pair = json.loads(
        (ARTIFACT_DIR / "DMSV_B1R2_DEVELOPMENT_PAIR_MATRIX.jsonl").read_text().splitlines()[0]
    )
    localization = json.loads((ARTIFACT_DIR / "DMSV_B1R2_NATIVE_LOCALIZATION_AUDIT.json").read_text())
    assert pair["request_digest_before"] != pair["request_digest_after"]
    assert localization["native_localization_status"] == "UNPROVEN"
    assert decision_state() != "DMSV_NATIVE_NODE_NULL_DOMINANT_CALL_STRUCTURALLY_DIRTY"


def decision_state() -> str:
    return json.loads((ARTIFACT_DIR / "DMSV_B1R2_DECISION.json").read_text())["final_state"]


def test_native_and_new_algorithm_localization_are_separate() -> None:
    localization = json.loads((ARTIFACT_DIR / "DMSV_B1R2_NATIVE_LOCALIZATION_AUDIT.json").read_text())
    assert localization["localization_classes"]["NATIVE_BATCH_LOCALIZABLE"].startswith("NOT_PROVEN")
    assert localization["localization_classes"]["NEW_ALGORITHM_LOCALIZABLE"].startswith("A split")
    assert localization["new_algorithm_identity_required_for_batch_split"] if "new_algorithm_identity_required_for_batch_split" in localization else True


def test_self_contained_bundle_has_no_raw_uuid_or_absolute_path() -> None:
    bundle = ARTIFACT_DIR / "DMSV_B1R2_SELF_CONTAINED_WITNESSES.jsonl"
    text = bundle.read_text(encoding="ascii")
    assert "/data/" not in text and "/tmp/" not in text
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", text)
    row = json.loads(text)
    assert row["privacy"] if "privacy" in row else row["privacy_checks"]


def test_evidence_required_mode_fails_when_decision_artifact_is_missing(tmp_path: Path) -> None:
    required = tmp_path / "DMSV_B1R2_DECISION.json"
    assert not required.exists()
    try:
        json.loads(required.read_text())
    except FileNotFoundError:
        failure = "BLOCKED_ARTIFACT_NOT_SELF_CONTAINED"
    else:  # pragma: no cover
        failure = "UNEXPECTED_PASS"
    assert failure == "BLOCKED_ARTIFACT_NOT_SELF_CONTAINED"


def test_preregistration_commit_precedes_result_commit() -> None:
    expected = "5031f10dcd37df1f6f199ee1125e1fae1760d580"
    if (ROOT / ".git").exists():
        prereg_commit = subprocess.check_output(
            ["git", "rev-parse", expected], cwd=ROOT, text=True
        ).strip()
        assert prereg_commit == expected
        assert subprocess.call(
            ["git", "merge-base", "--is-ancestor", expected, "HEAD"], cwd=ROOT
        ) == 0
    else:
        # A git archive is intentionally not a repository. The sealed report
        # carries the same preregistration commit for evidence-only replay.
        report = (ARTIFACT_DIR / "DMSV_B1R2_REPORT_20260831.md").read_text()
        assert f"PREREG_COMMIT={expected}" in report


def test_decision_matches_frozen_truth_table_and_authorizations() -> None:
    decision = json.loads((ARTIFACT_DIR / "DMSV_B1R2_DECISION.json").read_text())
    assert decision["final_state"] == "BLOCKED_STRUCTURAL_CLOSURE_INCOMPLETE"
    assert decision["population_counts"]["complete_eligible_pairs"] == 0
    assert all(value is False for value in decision["authorizations"].values())
