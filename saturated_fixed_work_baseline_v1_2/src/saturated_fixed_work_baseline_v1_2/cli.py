"""Protocol command line entry points."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Any, Sequence

from .recovery_runner import run_external_recovery
from .report import ReportError, build_final_report
from .stop_supersession import (
    SUPERSESSION_NAME,
    StopSupersessionError,
    materialize_stop_supersession,
    verify_stop_supersession,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sfwb-v1-2")
    commands = parser.add_subparsers(dest="command", required=True)
    recovery = commands.add_parser("external-recovery")
    recovery.add_argument("--run-root", type=Path, required=True)
    recovery.add_argument("--ssh-alias", default="zju-liuyi")
    recovery.add_argument("--rounds", type=int, default=3)
    recovery.add_argument("--interval-seconds", type=float, default=2.0)
    for name in (
        "preflight",
        "run-qualification",
        "run-main",
        "run-qa",
        "build-report",
    ):
        command = commands.add_parser(name)
        command.add_argument("--run-root", type=Path, required=True)
    return parser


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _active_stop(run_root: Path) -> dict[str, object] | None:
    root = run_root.resolve()
    supersession = root / SUPERSESSION_NAME
    if supersession.is_symlink() or supersession.exists():
        try:
            verify_stop_supersession(root)
        except (StopSupersessionError, OSError, TypeError, ValueError):
            return {
                "status": "BLOCKED_EXTERNAL_RESOURCE_IDENTITY",
                "reason": "STOP_SUPERSESSION_INVALID",
            }
        return None
    path = root / "STOP_WITH_EXTERNAL_DIAGNOSIS.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "status": "BLOCKED_EXTERNAL_RESOURCE_IDENTITY",
            "reason": "STOP_DIAGNOSIS_UNREADABLE",
        }
    if not isinstance(value, dict):
        return {
            "status": "BLOCKED_EXTERNAL_RESOURCE_IDENTITY",
            "reason": "STOP_DIAGNOSIS_INVALID",
        }
    candidate = dict(value)
    observed = candidate.pop("payload_sha256", None)
    if observed != _hash(candidate) or value.get("completed") is not False:
        return {
            "status": "BLOCKED_EXTERNAL_RESOURCE_IDENTITY",
            "reason": "STOP_DIAGNOSIS_INVALID",
        }
    return {
        "status": "BLOCKED_EXTERNAL_RESOURCE_IDENTITY",
        "completed": False,
        "resume_from_gate": value.get("resume_from_gate"),
        "missing_evidence": value.get("missing_evidence"),
        "next_action": value.get("next_action"),
        "stop_payload_sha256": observed,
    }


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _default_workflows() -> dict[str, Callable[[Path], Mapping[str, Any]]]:
    from .production_workflow import (
        run_formal_workflow,
        run_preflight_workflow,
        run_qa_workflow,
        run_qualification_workflow,
    )

    return {
        "preflight": run_preflight_workflow,
        "run-qualification": run_qualification_workflow,
        "run-main": run_formal_workflow,
        "run-qa": run_qa_workflow,
    }


def _guarded_stage(
    command: str,
    run_root: Path,
    *,
    workflows: Mapping[str, Callable[[Path], Mapping[str, Any]]] | None = None,
) -> int:
    root = run_root.resolve()
    blocked = _active_stop(root)
    if (
        command == "preflight"
        and blocked is not None
        and isinstance(blocked.get("stop_payload_sha256"), str)
    ):
        try:
            materialize_stop_supersession(root)
        except (StopSupersessionError, OSError, TypeError, ValueError):
            pass
        else:
            blocked = _active_stop(root)
    if blocked is not None:
        _print({**blocked, "command": command, "run_root": str(root)})
        return 3
    if command == "build-report":
        try:
            result = build_final_report(root)
        except ReportError as error:
            _print(
                {
                    "status": "NOT_READY",
                    "command": command,
                    "reason": str(error),
                    "run_root": str(root),
                }
            )
            return 3
        _print(
            {
                "status": "COMPLETE",
                "command": command,
                "final_seal_sha256": result["payload_sha256"],
                "run_root": str(root),
            }
        )
        return 0
    selected = dict(workflows) if workflows is not None else _default_workflows()
    workflow = selected.get(command)
    if not callable(workflow):
        _print(
            {
                "status": "NOT_READY",
                "command": command,
                "reason": "STAGE_WORKFLOW_UNAVAILABLE",
                "run_root": str(root),
            }
        )
        return 3
    try:
        result = dict(workflow(root))
    except Exception as error:
        _print(
            {
                "status": "NOT_READY",
                "command": command,
                "reason": str(error) or type(error).__name__,
                "error_type": f"{type(error).__module__}.{type(error).__qualname__}",
                "run_root": str(root),
            }
        )
        return 3
    _print({**result, "command": command, "run_root": str(root)})
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "external-recovery":
        diagnosis = run_external_recovery(
            run_root=args.run_root,
            ssh_alias=args.ssh_alias,
            rounds=args.rounds,
            interval_s=args.interval_seconds,
        )
        print(
            json.dumps(
                {
                    "status": diagnosis["status"],
                    "completed": diagnosis["completed"],
                    "payload_sha256": diagnosis["payload_sha256"],
                    "run_root": str(args.run_root.resolve()),
                },
                sort_keys=True,
            )
        )
        return 3
    if args.command in {
        "preflight",
        "run-qualification",
        "run-main",
        "run-qa",
        "build-report",
    }:
        return _guarded_stage(args.command, args.run_root)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
