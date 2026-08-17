#!/usr/bin/env python3
"""Create the documentation-only final methodology evidence envelope."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.final_methodology_envelope import (
    FinalMethodologyEnvelopeError,
    finalize_final_methodology_envelope,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--methodology", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        envelope = finalize_final_methodology_envelope(
            repository_root=args.repository_root,
            baseline_path=args.baseline,
            overlay_path=args.overlay,
            report_path=args.report,
            decision_path=args.decision,
            methodology_path=args.methodology,
            junit_path=args.junit,
            output_path=args.output,
        )
    except FinalMethodologyEnvelopeError as error:
        print(
            "STOP final_methodology_envelope "
            f"error_class={type(error).__module__}.{type(error).__qualname__}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    print(
        json.dumps(
            {
                "status": envelope["status"],
                "authority_effect": envelope["authority_effect"],
                "payload_sha256": envelope["payload_sha256"],
                "tests": envelope["tdd"]["tests"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
