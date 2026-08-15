#!/usr/bin/env python3
"""Finalize the one retry-005 source-7 read-only diagnosis authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_controller import _legacy_episodes
from paper_eval.s4_edge_identity_diagnosis_authority import (
    build_diagnosis_authority,
    write_diagnosis_authority_exclusive,
)
from paper_eval.s4_edge_identity_diagnosis_controller import (
    DEFAULT_AUTHORITY,
    DEFAULT_DATASET,
    DEFAULT_SPLIT,
    build_episode_manifest,
    evidence_sha256,
    source_sha256,
    validate_retry005_state,
    _json,
    _jsonl,
    CAPTURE_RUN,
    REPLAY_RUN,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_AUTHORITY)
    args = parser.parse_args()

    validate_retry005_state(
        capture_phase=_json(CAPTURE_RUN / "phase_result.json"),
        replay_phase=_json(REPLAY_RUN / "phase_result.json"),
        replay_checkpoint=_json(REPLAY_RUN / "checkpoint.json"),
        replay_events=_jsonl(REPLAY_RUN / "events.jsonl"),
    )
    episodes = _legacy_episodes(args.dataset, args.split)
    _, manifest_sha256 = build_episode_manifest(episodes)
    authority = build_diagnosis_authority(
        source_hash=episodes[7].source_hash,
        episode_manifest_sha256=manifest_sha256,
        evidence_sha256=evidence_sha256(args.dataset, args.split),
        source_sha256=source_sha256(),
    )
    write_diagnosis_authority_exclusive(args.output, authority)
    print(
        json.dumps(
            {
                "authority_file_sha256": sha256_file(args.output),
                "authority_sha256": authority["authority_sha256"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
