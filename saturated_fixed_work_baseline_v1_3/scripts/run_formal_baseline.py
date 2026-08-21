#!/usr/bin/env python
"""Pinned-environment launcher for the v1.3 B0/B1 formal baseline."""

from __future__ import annotations

import argparse
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.formal_baseline import run_formal_baseline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    result = run_formal_baseline(args.run_root)
    print(result, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
