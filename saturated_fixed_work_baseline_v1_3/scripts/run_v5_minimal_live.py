#!/usr/bin/env python3
"""P8 minimal-live preflight with an explicit authorization gate.

This command performs only read-only checks until a state file explicitly
authorizes the V5 action.  It never reuses another experiment's live grant.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from saturated_fixed_work_baseline_v1_3.membind_v5.campaign import verify_baseline_reference

try:
    from current_state_gate import LiveAction, LiveActionDenied, require_live_action
except ImportError:  # pragma: no cover - only reached when invoked without validation PYTHONPATH
    LiveAction = None  # type: ignore[assignment]
    LiveActionDenied = RuntimeError  # type: ignore[assignment,misc]
    require_live_action = None  # type: ignore[assignment]


def _models(url: str, expected: str, max_model_len: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
        parsed = json.loads(body)
        data = parsed.get("data", []) if isinstance(parsed, dict) else []
        ids = {str(item.get("id", "")).casefold() for item in data if isinstance(item, dict)}
        return {"status": "PASS" if expected.casefold() in ids else "FAIL", "url": url, "model_ids": sorted(ids), "body_sha256": __import__("hashlib").sha256(body).hexdigest(), "expected_model": expected, "expected_max_model_len": max_model_len}
    except Exception as exc:
        return {"status": "FAIL", "url": url, "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[2])
    parser.add_argument("--baseline-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--state", default="membind-validation/CURRENT_STATE.json")
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--live-root")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "schema_version": "membind.v5.p8-minimal-preflight.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline": {"root": str(Path(args.baseline_root).resolve())},
        "construction": _models("http://10.87.5.247:8000/v1/models", "qwen3-32b-fp8", 65536),
        "embedding": _models("http://10.87.5.247:8001/v1/models", "qwen3-embedding-0.6b", 32768),
    }
    try:
        evidence["baseline"] = verify_baseline_reference(args.baseline_root, allow_invalid_qa=True)
    except Exception as exc:
        evidence["status"] = "FAIL"
        evidence["blocker"] = f"BASELINE_REFERENCE:{type(exc).__name__}"
        output.write_text(json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": evidence["status"], "blocker": evidence["blocker"]}, sort_keys=True))
        return 2
    state_path = Path(args.state)
    if not state_path.is_absolute():
        state_path = Path(args.repo_root) / state_path
    try:
        if require_live_action is None or LiveAction is None:
            raise RuntimeError("current_state_gate_unavailable")
        decision = require_live_action(
            LiveAction.MEMBIND_V5,
            state_path=state_path,
        )
    except LiveActionDenied as exc:
        evidence["status"] = "BLOCKED_AUTHORITY"
        evidence["blocker"] = f"V5_AUTHORITY:{exc.reason}"
    except Exception as exc:
        evidence["status"] = "FAIL"
        evidence["blocker"] = f"V5_AUTHORITY_CHECK:{type(exc).__name__}"
    else:
        evidence["status"] = "READY_FOR_MINIMAL"
        evidence["state_path"] = str(state_path.resolve())
        evidence["authorization"] = {
            "action": decision.action,
            "reason": decision.reason,
            "native_characterization_c5_reused": False,
        }
    output.write_text(json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "output": str(output), "blocker": evidence.get("blocker")}, sort_keys=True))
    if evidence["status"] != "READY_FOR_MINIMAL" or not args.execute_live:
        return 0 if evidence["status"] in {"READY_FOR_MINIMAL", "BLOCKED_AUTHORITY"} else 2

    if not args.live_root:
        raise SystemExit("--live-root is required with --execute-live")
    try:
        from native_characterization_instrumentation import install_native_characterization_instrumentation
        from native_characterization_runtime import build_u0_graphiti_from_env
        from native_characterization_tracing import TraceRecorder
        from live_outputs import export_canonical_graph
        from saturated_fixed_work_baseline_v1_2.dataset import load_episode_inputs
        from saturated_fixed_work_baseline_v1_3.membind_v5.live_runner import P8LiveConfig, run_p8_minimal_live_async
    except Exception as exc:
        evidence["status"] = "FAIL"
        evidence["blocker"] = f"LIVE_IMPORT:{type(exc).__name__}"
        output.write_text(json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 2

    def runtime_builder() -> Any:
        def check(action: Any, **kwargs: Any) -> Any:
            return require_live_action(action, state_path=state_path, **kwargs)
        return build_u0_graphiti_from_env(
            authorization_checker=check,
            live_action=LiveAction.MEMBIND_V5,
        )

    try:
        live_result = __import__("asyncio").run(
            run_p8_minimal_live_async(
                P8LiveConfig(
                    root=Path(args.live_root),
                    baseline_root=Path(args.baseline_root),
                    state_path=state_path,
                    run_id=Path(args.live_root).name,
                ),
                runtime_builder=runtime_builder,
                authorization_checker=require_live_action,
                episode_loader=load_episode_inputs,
                instrumentation_installer=install_native_characterization_instrumentation,
                recorder_factory=TraceRecorder,
                graph_exporter=export_canonical_graph,
            )
        )
    except Exception as exc:
        evidence["status"] = "FAIL"
        evidence["blocker"] = f"LIVE_EXECUTION:{type(exc).__module__}.{type(exc).__name__}"
        evidence["error"] = str(exc)[:240]
        output.write_text(json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 2
    evidence["status"] = "P8_LIVE_SEALED"
    evidence["live_result"] = {"root": live_result.get("root"), "seal": live_result.get("seal")}
    output.write_text(json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "root": live_result.get("root")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
