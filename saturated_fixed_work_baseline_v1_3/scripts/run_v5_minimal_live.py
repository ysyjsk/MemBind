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
        evidence["baseline"] = verify_baseline_reference(args.baseline_root)
    except Exception as exc:
        evidence["status"] = "FAIL"
        evidence["blocker"] = f"BASELINE_REFERENCE:{type(exc).__name__}"
        output.write_text(json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"status": evidence["status"], "blocker": evidence["blocker"]}, sort_keys=True))
        return 2
    state_path = Path(args.repo_root) / args.state
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        evidence["status"] = "BLOCKED_AUTHORITY"
        evidence["blocker"] = f"CURRENT_STATE_UNREADABLE:{type(exc).__name__}"
    else:
        actions = state.get("authorized_live_actions", []) if isinstance(state, dict) else []
        if "membind_v5" not in actions:
            evidence["status"] = "BLOCKED_AUTHORITY"
            evidence["blocker"] = "V5_LIVE_ACTION_NOT_AUTHORIZED"
            evidence["authorized_live_actions"] = list(actions) if isinstance(actions, list) else actions
            evidence["required_authority"] = "Add an explicit V5 live action/state scope through the repository's state transition protocol; do not reuse native_characterization_c5."
        else:
            evidence["status"] = "READY_FOR_MINIMAL"
    output.write_text(json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": evidence["status"], "output": str(output), "blocker": evidence.get("blocker")}, sort_keys=True))
    return 0 if evidence["status"] in {"READY_FOR_MINIMAL", "BLOCKED_AUTHORITY"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

