#!/usr/bin/env python3
"""Seal the data-conditioned methodology decision from finalized artifacts.

This command performs no live I/O.  Existing output is accepted only when it
is byte-for-byte equivalent after JSON parsing; conflicting output fails closed.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from paper_eval.artifacts import (
    atomic_write_json,
    canonical_bytes,
    payload_sha256,
    sha256_file,
)
from paper_eval.methodology_decision import (
    MethodologyDecisionError,
    build_methodology_decision,
)


_PINNED_C3_FILE_SHA256 = (
    "a80ca5a8e763c19eea9d2cde1dbe001425200d04c857384cb862cc65ccf1887f"
)
_PINNED_C3_PAYLOAD_SHA256 = (
    "7adc924db06e33e319d973a9b6ceaf402866bda4ea38a8755d3781f2ca86449f"
)
_PINNED_C5_FILE_SHA256 = (
    "00ebfe67c13758a02fbb2dcbc94a336de92f88dbe25e666b3e069d7737c3594d"
)
_PINNED_C5_PAYLOAD_SHA256 = (
    "73cfc5219c39e9e786e9353868f5c64d942fec1db1188858fa314763ad6f8dc7"
)
_PINNED_C5_EVENTS_SHA256 = (
    "52a69edd8ff94c1eaca5ca00401ccb75e3d4f39dc326364cbdf3e322ead5e849"
)


def _load(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise MethodologyDecisionError(f"{label} is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MethodologyDecisionError(f"{label} is not a JSON object")
    return value


def _verify_characterization(value: Mapping[str, Any]) -> str:
    if (
        value.get("schema_version")
        != "membind.native-characterization-e2-opportunity.v1"
        or value.get("status") != "complete"
        or value.get("stage") != "C3/E2"
    ):
        raise MethodologyDecisionError("C3 characterization identity is invalid")
    stored = value.get("payload_sha256")
    if not isinstance(stored, str):
        raise MethodologyDecisionError("C3 characterization payload seal is invalid")
    body = {key: item for key, item in value.items() if key != "payload_sha256"}
    if stored != payload_sha256(body):
        raise MethodologyDecisionError("C3 characterization payload seal mismatch")
    return stored


def _verify_existing_decision(value: Mapping[str, Any]) -> None:
    if (
        value.get("schema_version")
        != "membind.paper-eval-v3.methodology-decision.v1"
        or value.get("status") != "PASS"
    ):
        raise MethodologyDecisionError(
            "existing methodology decision identity is invalid"
        )
    stored = value.get("payload_sha256")
    body = {key: item for key, item in value.items() if key != "payload_sha256"}
    if not isinstance(stored, str) or stored != payload_sha256(body):
        raise MethodologyDecisionError(
            "existing methodology decision payload seal mismatch"
        )


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    report_path = args.report.resolve()
    c3_path = args.c3.resolve()
    c5_path = args.c5.resolve()
    c5_events_path = args.c5_events.resolve()
    output_path = args.output.resolve()

    report = _load(report_path, label="development report")
    c3 = _load(c3_path, label="C3 characterization")
    c5 = _load(c5_path, label="C5 result")
    if not c5_events_path.is_file():
        raise MethodologyDecisionError(f"C5 events are missing: {c5_events_path}")

    c3_file_sha = sha256_file(c3_path)
    c5_file_sha = sha256_file(c5_path)
    c5_events_sha = sha256_file(c5_events_path)
    if c3_file_sha != _PINNED_C3_FILE_SHA256:
        raise MethodologyDecisionError("pinned C3 file SHA256 drift")
    if c5_file_sha != _PINNED_C5_FILE_SHA256:
        raise MethodologyDecisionError("pinned C5 file SHA256 drift")
    if c5_events_sha != _PINNED_C5_EVENTS_SHA256:
        raise MethodologyDecisionError("pinned C5 events SHA256 drift")
    c3_payload_sha = _verify_characterization(c3)
    if c3_payload_sha != _PINNED_C3_PAYLOAD_SHA256:
        raise MethodologyDecisionError("pinned C3 payload SHA256 drift")
    if c5.get("payload_sha256") != _PINNED_C5_PAYLOAD_SHA256:
        raise MethodologyDecisionError("pinned C5 payload SHA256 drift")

    decision = build_methodology_decision(
        decision_run_id=args.decision_run_id,
        report=report,
        c5_result=c5,
        report_file_sha256=sha256_file(report_path),
        c5_file_sha256=c5_file_sha,
        characterization_file_sha256=c3_file_sha,
        characterization_payload_sha256=c3_payload_sha,
        c5_events_file_sha256=c5_events_sha,
    )
    if output_path.exists():
        existing = _load(output_path, label="existing methodology decision")
        _verify_existing_decision(existing)
        if canonical_bytes(existing) != canonical_bytes(decision):
            raise MethodologyDecisionError(
                "existing methodology decision conflicts with sealed inputs"
            )
    else:
        atomic_write_json(output_path, decision)
    return decision


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("decision_run_id")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--c3", required=True, type=Path)
    parser.add_argument("--c5", required=True, type=Path)
    parser.add_argument("--c5-events", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    decision = finalize(_parser().parse_args())
    print(
        json.dumps(
            {
                "status": decision["status"],
                "decision_run_id": decision["decision_run_id"],
                "actual_decision_matrix_cell": decision[
                    "actual_decision_matrix_cell"
                ],
                "problem_verdict": decision["problem_verdict"],
                "payload_sha256": decision["payload_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
