#!/usr/bin/env python3
"""Offline inspection CLI for the isolated MAB Quality v2 lane."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mab_quality_v2_final_qa.dataset_adapter import MABDatasetAdapter
from mab_quality_v2_final_qa.qualification import qualify_records
from mab_quality_v2_final_qa.runtime_gate import (
    check_model_port,
    require_live_model_ports,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect_parser = sub.add_parser(
        "inspect", help="qualify an offline MAB JSON/JSONL file"
    )
    inspect_parser.add_argument("dataset", type=Path)
    inspect_parser.add_argument("--source", default=None)
    inspect_parser.add_argument("--revision", default="UNPINNED")
    qualify_parser = sub.add_parser(
        "qualify", help="report every selected context mapping defect"
    )
    qualify_parser.add_argument("dataset", type=Path)
    qualify_parser.add_argument("--source", default=None)
    qualify_parser.add_argument("--revision", default="UNPINNED")
    health_parser = sub.add_parser("health", help="read-only model-port check")
    health_parser.add_argument(
        "--live", action="store_true", help="require both model ports"
    )
    live_parser = sub.add_parser(
        "run-live", help="run the isolated MAB workflow against frozen services"
    )
    live_parser.add_argument("--dataset", type=Path, required=True)
    live_parser.add_argument("--artifact-root", type=Path, required=True)
    live_parser.add_argument("--run-id", required=True)
    live_parser.add_argument(
        "--revision",
        default="hf:ai-hyz/MemoryAgentBench@7ea066982b140a19337e17e60d45d4076e042faf",
    )
    live_parser.add_argument("--history-limit", type=int, choices=(1, 4), default=4)
    live_parser.add_argument("--mode", choices=("smoke", "full"), default="full")
    args = parser.parse_args(argv)
    if args.command == "run-live":
        import asyncio

        from mab_quality_v2_final_qa.live_workflow import run_quality_workflow

        try:
            result = asyncio.run(
                run_quality_workflow(
                    dataset_path=args.dataset,
                    artifact_root=args.artifact_root,
                    run_id=args.run_id,
                    revision=args.revision,
                    history_limit=args.history_limit,
                    mode=args.mode,
                )
            )
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            print(
                f"LIVE_WORKFLOW_FAILED:{type(error).__name__}:{error}",
                file=sys.stderr,
            )
            return 2
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "health":
        try:
            statuses = (
                require_live_model_ports()
                if args.live
                else tuple(check_model_port(port) for port in (8002, 8003))
            )
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            return 2
        print(
            json.dumps(
                [status.__dict__ for status in statuses], ensure_ascii=False, indent=2
            )
        )
        return 0
    if args.command == "qualify":
        try:
            raw = json.loads(args.dataset.read_text(encoding="utf-8"))
            records = (
                raw.get("data", []) if isinstance(raw, dict) and "data" in raw else raw
            )
            if isinstance(records, dict):
                records = [records]
            result = qualify_records(
                records, source=args.source, dataset_revision=args.revision
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["live_authorized"] else 2
        except (OSError, TypeError, ValueError) as error:
            print(f"DATASET_MAPPING_INVALID:{error}", file=sys.stderr)
            return 2
    try:
        adapter = MABDatasetAdapter.from_file(
            args.dataset, source=args.source, dataset_revision=args.revision
        )
        print(json.dumps(adapter.manifest, ensure_ascii=False, indent=2))
    except (OSError, TypeError, ValueError) as error:
        print(f"DATASET_MAPPING_INVALID:{error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
