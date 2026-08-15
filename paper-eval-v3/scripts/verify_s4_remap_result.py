#!/usr/bin/env python3
"""Independently verify the completed S4 candidate-remap smoke result."""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_remap_result import verify_s4_remap_smoke_result


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
AUTHORITY = NATIVE / "S4_REMAP_SMOKE_AUTHORIZATION_RETRY_005.json"
CONSUMPTION = (
    NATIVE
    / "runs/s4-remap-smoke-retry-005/S4_REMAP_AUTHORITY_CONSUMPTION.json"
)
CAPTURE = NATIVE / "runs/s4-d0-capture-20260815-005/phase_result.json"
REPLAY = NATIVE / "runs/s4-d0-replay-20260815-005/phase_result.json"
RESULT = NATIVE / "S4_D0_REMAP_SMOKE_RESULT.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    artifact = verify_s4_remap_smoke_result(
        result=_load(RESULT),
        authority=_load(AUTHORITY),
        authority_file_sha256=sha256_file(AUTHORITY),
        consumption=_load(CONSUMPTION),
        consumption_file_sha256=sha256_file(CONSUMPTION),
        capture_result=_load(CAPTURE),
        capture_result_file_sha256=sha256_file(CAPTURE),
        replay_result=_load(REPLAY),
        replay_result_file_sha256=sha256_file(REPLAY),
    )
    evaluation = artifact["payload"]["evaluation"]
    print(
        json.dumps(
            {
                "verdict": evaluation["verdict"],
                "canonical_graph_parity": evaluation[
                    "canonical_graph_parity"
                ],
                "cache_mutation_during_replay": evaluation[
                    "cache_mutation_during_replay"
                ],
                "candidate_remap_hit_count": evaluation[
                    "candidate_remap_hit_count"
                ],
                "candidate_oracle_resolution_accounting": evaluation[
                    "candidate_oracle_resolution_accounting"
                ],
                "result_file_sha256": sha256_file(RESULT),
                "result_payload_sha256": artifact["payload_sha256"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
