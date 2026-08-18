#!/usr/bin/env python3
"""Seal one public provider envelope from restricted startup-log evidence.

The command accepts only SHA256 identities for the two startup-log snapshots;
it never copies logs, credentials, prompts, or model responses into artifacts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from paper_eval.membind_v31.provider_envelope import (  # noqa: E402
    build_provider_execution_envelope,
    write_provider_execution_envelope,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--construction-log-sha256", required=True)
    parser.add_argument("--embedding-log-sha256", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT
        / "artifacts/paper_eval/membind_v31/PROVIDER_EXECUTION_ENVELOPE_XGRAMMAR_20260818.json",
    )
    args = parser.parse_args(argv)
    artifact = build_provider_execution_envelope(
        startup_evidence={
            "observation_transport": "restricted-ssh-read",
            "construction_startup_log_sha256": args.construction_log_sha256,
            "embedding_startup_log_sha256": args.embedding_log_sha256,
        }
    )
    write_provider_execution_envelope(args.output, artifact)
    print(
        f"status=PASS provider_execution_envelope_sha256={artifact['payload_sha256']} "
        f"path={args.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
