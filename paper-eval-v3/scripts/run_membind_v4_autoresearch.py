#!/usr/bin/env python3
"""Run one bounded v4 autoresearch candidate.

The default is a fail-closed live candidate.  Use ``--mode fixture`` only for
offline TDD verification; fixture artifacts are explicitly labelled and are
never formal-table eligible.  A read-only ``--preflight-only`` invocation is
safe under a restricted Codex sandbox and records the exact classification.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.artifacts import atomic_write_json  # noqa: E402
from paper_eval.membind_v4.live_preflight import (  # noqa: E402
    probe_services,
    read_env_file,
)
from paper_eval.membind_v4.autoresearch import candidate_config  # noqa: E402
from paper_eval.membind_v4.production_runner import (  # noqa: E402
    V4ProductionRunnerError,
    build_v4_candidate_live_runner,
    verify_prior_six_reduction,
)
from paper_eval.membind_v4.runner import run_candidate  # noqa: E402


def _default_root() -> Path:
    return PROJECT / "artifacts/paper_eval/membind_v4/autoresearch"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=("c01", "c02", "c03"), default="c01")
    parser.add_argument("--history-id", default="07741c45")
    parser.add_argument("--source-count", type=int, choices=(6, 12, 20), default=6)
    parser.add_argument("--protocol-amendment", default=None)
    parser.add_argument("--a1-audit", type=Path, default=None)
    parser.add_argument("--a1-amendment", type=Path, default=None)
    parser.add_argument("--policy", default=None)
    parser.add_argument("--fresh-namespace", action="store_true")
    parser.add_argument(
        "--prior-six-reduction",
        type=Path,
        default=None,
        help="sealed c01 six-source reduction required for a 12-source extension",
    )
    parser.add_argument("--mode", choices=("live", "fixture", "blocked"), default="live")
    parser.add_argument("--env-file", type=Path, default=PROJECT.parent / "membind-validation/.env")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args(argv)

    if args.policy is not None and args.policy != candidate_config(args.candidate)["policy"]:
        parser.error("candidate_policy_drift")

    if args.source_count == 20:
        if args.candidate != "c01":
            parser.error("a1_candidate_not_c01")
        if args.protocol_amendment != "A1":
            parser.error("a1_protocol_amendment_required")
        if args.a1_audit is None:
            args.a1_audit = PROJECT / "artifacts/paper_eval/membind_v4/protocol_amendment_a1/V4_OPPORTUNITY_AUDIT_A1.json"
        if args.a1_amendment is None:
            args.a1_amendment = PROJECT / "artifacts/paper_eval/membind_v4/protocol_amendment_a1/V4_PROTOCOL_AMENDMENT_A1_OPPORTUNITY_EXPOSURE.json"
    elif args.protocol_amendment is not None or args.a1_audit is not None or args.a1_amendment is not None:
        parser.error("a1_protocol_amendment_unexpected")

    if args.source_count == 12:
        if args.prior_six_reduction is None:
            parser.error("prior_six_reduction_required")
        try:
            verify_prior_six_reduction(
                args.prior_six_reduction,
                candidate_id=args.candidate,
                history_id=args.history_id,
            )
        except V4ProductionRunnerError as error:
            parser.error(str(error))
    elif args.prior_six_reduction is not None:
        parser.error("prior_six_reduction_unexpected")

    root = args.output_root
    if root is None:
        root = _default_root() / f"membind-v4-ar-{time.strftime('%Y%m%d-%H%M%S')}-{args.candidate}"
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    env = read_env_file(args.env_file)
    preflight = None
    if args.mode == "live" or args.preflight_only:
        preflight = probe_services(env=env, timeout=args.timeout)
        atomic_write_json(root / "PREFLIGHT.json", preflight)
    if args.preflight_only:
        print(json.dumps({"status": preflight["status"], "classification": preflight["classification"], "path": str(root / "PREFLIGHT.json")}, sort_keys=True))
        return 0 if preflight["status"] == "READY" else 2

    if args.mode == "blocked" and preflight is None:
        preflight = {"status": "BLOCKED", "classification": "SERVICE_PREFLIGHT_BLOCKED"}
    live_runner = None
    if args.mode == "live":
        # Build the runner only after preflight succeeds.  Its production
        # context remains lazy, so a blocked probe never imports Graphiti or
        # creates a namespace.
        if not isinstance(preflight, dict) or preflight.get("status") != "READY":
            live_runner = None
        else:
            live_runner = build_v4_candidate_live_runner(
                prior_six_reduction_path=args.prior_six_reduction,
                protocol_amendment=args.protocol_amendment,
                a1_audit_path=args.a1_audit,
                a1_amendment_path=args.a1_amendment,
            )
    result = run_candidate(
        candidate_id=args.candidate,
        history_id=args.history_id,
        source_count=args.source_count,
        output_root=root,
        mode=args.mode,
        preflight=preflight,
        live_runner=live_runner,
        protocol_amendment=args.protocol_amendment,
        a1_audit_path=args.a1_audit,
        a1_amendment_path=args.a1_amendment,
    )
    print(
        json.dumps(
            {
                "status": result.get("status"),
                "candidate_id": args.candidate,
                "history_id": args.history_id,
                "source_count": args.source_count,
                "root": str(root),
                "formal_main_table_eligible": False,
            },
            sort_keys=True,
        )
    )
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
