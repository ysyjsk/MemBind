"""Reproduce the Protocol v1.3 split from the immutable legacy split.

The v1.2 generator remains untouched so its historical source hash stays valid.
This module applies only the exposure-based quarantine introduced by v1.3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


GENERATOR_VERSION = "membind-validation.dataset-v1.3-exposure-quarantine.v1"
PROTOCOL_VERSION = "current-validation-v1.3"
GENERATOR_PATH = "src/dataset_v1_3.py"
LEGACY_SPLIT_DISPLAY_PATH = "artifacts/dataset/frozen_split.json"
SELECTION_RULE = (
    "retain_original_four_calibration_ids; "
    "quarantine_previously_inspected_ids; "
    "choose_first_eight_remaining_eligible_ids_by_sha256_question_id_order"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_records(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("LongMemEval source must be a JSON list")
    return payload


def _eligible_ids(records: Iterable[dict[str, Any]]) -> list[str]:
    ids = [
        str(record["question_id"])
        for record in records
        if record.get("question_type") == "knowledge-update"
        and not str(record.get("question_id", "")).endswith("_abs")
    ]
    if len(ids) != len(set(ids)):
        raise ValueError("eligible question IDs must be unique")
    return sorted(ids, key=lambda value: hashlib.sha256(value.encode()).hexdigest())


def derive_split_v1_3(
    *,
    data_path: Path,
    legacy_split_path: Path,
    quarantined_question_ids: Iterable[str],
    quarantine_reason: str,
) -> dict[str, Any]:
    """Return the deterministic v1.3 split without writing any file."""

    data_path = data_path.resolve()
    legacy_split_path = legacy_split_path.resolve()
    legacy = json.loads(legacy_split_path.read_text(encoding="utf-8"))
    source_sha256 = _sha256(data_path)
    if source_sha256 != legacy["source_sha256"]:
        raise ValueError("source hash does not match the immutable legacy split")

    calibration = list(legacy["calibration_question_ids"])
    legacy_evaluation = list(legacy["evaluation_question_ids"])
    quarantined = list(dict.fromkeys(str(value) for value in quarantined_question_ids))
    if not quarantined:
        raise ValueError("v1.3 requires an explicit exposure quarantine")
    if set(quarantined) & set(calibration):
        raise ValueError("calibration IDs cannot be exposure-quarantined here")
    if not set(quarantined).issubset(legacy_evaluation):
        raise ValueError("quarantined IDs must come from the legacy evaluation split")
    if not quarantine_reason.strip():
        raise ValueError("quarantine reason must be explicit")

    eligible = _eligible_ids(_load_records(data_path))
    remaining = [
        question_id
        for question_id in eligible
        if question_id not in set(calibration) | set(quarantined)
    ]
    evaluation = remaining[: len(legacy_evaluation)]
    if len(evaluation) != len(legacy_evaluation):
        raise ValueError("not enough eligible IDs to preserve evaluation cardinality")

    generator = Path(__file__).resolve()
    return {
        "protocol_version": PROTOCOL_VERSION,
        "calibration_question_ids": calibration,
        "compatibility_development_question_ids": quarantined,
        "evaluation_question_ids": evaluation,
        "selection_rule": SELECTION_RULE,
        "selection_uses_model_or_performance_results": False,
        "legacy_split_path": LEGACY_SPLIT_DISPLAY_PATH,
        "legacy_split_sha256": _sha256(legacy_split_path),
        "generator_script": GENERATOR_PATH,
        "generator_script_version": GENERATOR_VERSION,
        "generator_script_sha256": _sha256(generator),
        "source_path": str(data_path),
        "source_sha256": source_sha256,
        "quarantine_reason": quarantine_reason,
        "legacy_split_is_immutable": True,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--legacy-split", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()

    expected = json.loads(args.manifest.read_text(encoding="utf-8"))
    actual = derive_split_v1_3(
        data_path=args.data,
        legacy_split_path=args.legacy_split,
        quarantined_question_ids=expected[
            "compatibility_development_question_ids"
        ],
        quarantine_reason=expected["quarantine_reason"],
    )
    if actual != expected:
        raise SystemExit("v1.3 split manifest does not match deterministic derivation")
    print(json.dumps({"ok": True, "manifest_sha256": _sha256(args.manifest)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
