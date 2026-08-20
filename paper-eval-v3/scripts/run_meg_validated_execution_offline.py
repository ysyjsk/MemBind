#!/usr/bin/env python3
"""Generate MEG validated-execution offline gate artifacts only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
SOURCE = PROJECT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from paper_eval.artifacts import atomic_write_json  # noqa: E402
from paper_eval.membind_v4.mseg.offline_validation import (  # noqa: E402
    build_offline_validation_documents,
)


DEFAULT_CAPTURE_ROOT = (
    PROJECT
    / "artifacts/paper_eval/membind_v4/vdc_capture"
    / "membind-v31-opt-w4-vdc-capture-20260820-002"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT
    / "artifacts/paper_eval/membind_v4/meg_validated_execution"
    / "meg-validated-offline-20260821-001"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT)
    parser.add_argument("--graphiti-root", type=Path, default=None)
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--provider-free-test-count", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project = Path(args.project_root).resolve()
    graphiti = (
        Path(args.graphiti_root).resolve()
        if args.graphiti_root is not None
        else project.parent
        / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core"
    )
    capture = Path(args.capture_root).resolve()
    output = Path(args.output_root).resolve()
    if output.exists():
        raise ValueError("meg_offline_output_root_not_fresh")
    documents = build_offline_validation_documents(
        project_root=project,
        graphiti_root=graphiti,
        capture_bundle_path=capture / "VDC_CAPTURE_BUNDLE.json",
        replay_verification_path=capture / "VDC_CAPTURE_REPLAY_VERIFICATION.json",
        provider_free_test_count=args.provider_free_test_count,
    )
    output.mkdir(parents=True, exist_ok=False)
    for name, value in documents.items():
        path = output / name
        if isinstance(value, dict):
            atomic_write_json(path, value)
        else:
            path.write_text(value, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "STOP_INSTRUMENTATION_FAILURE",
                "output_root": str(output),
                "live_services_started": 0,
                "bounded_real_capture_started": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
