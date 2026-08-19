#!/usr/bin/env python3
"""Run or resume the exact frozen MemBind v4 four-history plan.

Live execution requires a READY read-only service preflight.  The pinned
production history runner is the default; ``module:function`` may be supplied
only for an explicitly injected test or deployment adapter.  Fixture
execution is available only for offline TDD and its artifacts are never
formal-table eligible.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.membind_v4.freeze import FORMAL_HISTORY_IDS  # noqa: E402
from paper_eval.membind_v4.full_run import run_v4_full  # noqa: E402
from paper_eval.membind_v4.live_block import build_v4_full_history_runner  # noqa: E402
from paper_eval.membind_v4.live_preflight import probe_services, read_env_file  # noqa: E402


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("preflight artifact must be an object")
    return value


def _load_runner(reference: str | None) -> Callable[..., Mapping[str, object]] | None:
    if reference is None:
        # The production runner is the default live path.  Keep construction
        # lazy so a blocked preflight never imports or initializes services.
        selected: Callable[..., Mapping[str, object]] | None = None

        def default_runner(**kwargs: object) -> Mapping[str, object]:
            nonlocal selected
            if selected is None:
                selected = build_v4_full_history_runner()
            return selected(**kwargs)

        return default_runner
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("history runner must use module:function syntax")
    value: Any = getattr(importlib.import_module(module_name), attribute)
    if not callable(value):
        raise ValueError("history runner is not callable")
    return value


def _default_run_id() -> str:
    return f"v4-full-{time.strftime('%Y%m%d-%H%M%S')}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-method", type=Path, required=True)
    parser.add_argument("--histories", default=",".join(FORMAL_HISTORY_IDS))
    parser.add_argument("--fresh-namespaces", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--mode", choices=("live", "fixture", "blocked"), default="live")
    parser.add_argument("--history-runner", default=None, help="Production callback as module:function")
    parser.add_argument("--preflight", type=Path, default=None)
    parser.add_argument("--env-file", type=Path, default=PROJECT.parent / "membind-validation/.env")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args(argv)

    if not args.fresh_namespaces:
        parser.error("--fresh-namespaces is required; the formal runner never reuses a namespace")
    histories = tuple(item.strip() for item in args.histories.split(",") if item.strip())
    run_id = args.run_id or _default_run_id()
    output_root = args.output_root
    if output_root is None:
        output_root = PROJECT / "artifacts/paper_eval/membind_v4/full" / run_id

    preflight: Mapping[str, object] | None = None
    if args.preflight is not None:
        preflight = _read_json(args.preflight)
    elif args.mode == "live":
        preflight = probe_services(env=read_env_file(args.env_file), timeout=args.timeout)
    elif args.mode == "blocked":
        preflight = {
            "status": "BLOCKED_SERVICE_PREFLIGHT",
            "classification": "SERVICE_PREFLIGHT_BLOCKED",
        }

    try:
        history_runner = (
            None
            if args.mode == "fixture" and args.history_runner is None
            else _load_runner(args.history_runner)
        )
        result = run_v4_full(
            frozen_method_path=args.frozen_method,
            output_root=output_root,
            run_id=run_id,
            histories=histories,
            mode=args.mode,
            preflight=preflight,
            history_runner=history_runner,
        )
    except (ImportError, AttributeError, OSError, ValueError) as error:
        print(json.dumps({"status": "ERROR", "error": str(error)}, sort_keys=True))
        return 2

    public = {
        "status": result.get("status"),
        "run_id": run_id,
        "history_ids": list(histories),
        "source_count": result.get("source_count"),
        "formal_main_table_eligible": result.get("formal_main_table_eligible", False),
        "classification": result.get("classification"),
        "root": str(Path(output_root).resolve()),
    }
    print(json.dumps(public, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
