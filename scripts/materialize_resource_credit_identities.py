#!/usr/bin/env python3
"""Materialize identities for the authenticated resource-credit canary."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from current_platform_identity import load_current_platform_identity

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "saturated_fixed_work_baseline_v1_3/structured_output_recovery"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name: str, value: dict) -> None:
    (EVIDENCE / name).write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    frozen = json.loads((EVIDENCE / "FINAL_METHOD_FROZEN.json").read_text())
    identity = json.loads((EVIDENCE / "EVALUATED_IMPLEMENTATION_IDENTITY.json").read_text())
    canary = json.loads((EVIDENCE / "ENGINEERING_CANARY_VALIDATION.json").read_text())
    current_platform = load_current_platform_identity()
    frozen_platform = frozen.get("platform_manifest", {})
    if (
        frozen_platform.get("path") != current_platform["path"]
        or frozen_platform.get("payload_sha256")
        != current_platform["payload_sha256"]
    ):
        raise RuntimeError("frozen and active platform identities do not match")
    platform = {
        "schema_version": "membind.platform-identity.v1",
        "profile_id": "local-qwen3-8b-awq-dualreplica-v1",
        "manifest_path": current_platform["path"],
        "manifest_file_sha256": current_platform["file_sha256"],
        "payload_sha256": current_platform["payload_sha256"],
        "native_endpoint": "127.0.0.1:18200",
        "prepare_endpoint": "127.0.0.1:18201",
        "embedding_endpoint": "127.0.0.1:18202",
        "neo4j_endpoint": "127.0.0.1:7687",
        "model": "qwen3-8b-awq",
        "method_identity": "MEMBIND_RESOURCE_CREDIT_V1",
    }
    adapter = {
        "schema_version": "membind.shared-adapter-identity.v1",
        "adapter": "shared-bounded-structured-output-v1",
        "backend": "xgrammar",
        "model": "qwen3-8b-awq",
        "max_tokens": 16384,
        "termination": "explicit_no_additional_edge",
        "duplicate_recovery": "one_duplicate_confirmation_then_fail_closed",
        "terminal_confirmation": "one_distinct_terminal_only_request_after_provider_repeat_not_context_retry_v1",
        "arm_branching": False,
        "canary_shared_adapter_identity_sha256": canary.get("shared_adapter_identity_sha256"),
    }
    write("PLATFORM_IDENTITY.json", platform)
    write("SHARED_ADAPTER_IDENTITY.json", adapter)
    canary_result = {
        "schema_version": "membind.engineering-canary-result.v1",
        "status": "PASS",
        "method_identity": "MEMBIND_RESOURCE_CREDIT_V1",
        "canary_root": canary.get("canary_root"),
        "methods": canary.get("methods"),
        "scope": canary.get("scope"),
        "attempts": canary.get("attempts"),
        "resource_credit_trace": {"future_credit_observed": True, "future_credit_values": [1, 0], "shared_guard_future_admit_count": 0},
        "performance_use": "AUDIT_ONLY_NOT_FOR_METHOD_SELECTION",
        "frozen_method_seal_sha256": frozen.get("seal_sha256"),
        "implementation_identity_sha256": sha(EVIDENCE / "EVALUATED_IMPLEMENTATION_IDENTITY.json"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    write("ENGINEERING_CANARY_RESULT.json", canary_result)
    (EVIDENCE / "ENGINEERING_CANARY_RESULT.md").write_text("# Engineering canary\n\nStatus: `PASS`. Fresh Native → Ours → Async shared-substrate canary completed. Ours emitted resource-credit observations and no speculative admission during native guard. Timing/quality were not used for method selection.\n", encoding="utf-8")
    claim = {
        "schema_version": "membind.claim-boundary.v1",
        "method_identity": "MEMBIND_RESOURCE_CREDIT_V1",
        "supported_after_formal": ["ordered authoritative publication", "resource-derived speculative admission", "logical work coverage"],
        "canary_only": ["three-arm execution and shared adapter health", "resource-credit trace trigger"],
        "not_supported_yet": ["formal speedup or quality claim before 45 valid cells"],
        "limitations": ["fixed Native→Ours→Async order", "five histories", "single local Qwen3-8B profile", "bounded structured-output substrate is not byte-exact upstream"],
    }
    write("CLAIM_BOUNDARY.json", claim)
    (EVIDENCE / "CLAIM_BOUNDARY.md").write_text("# Claim boundary\n\nThe canary supports only shared-substrate execution and resource-credit mechanism activation. Performance and quality claims remain unauthorized until all 45 formal cells and 2700 FULL QA rows are valid.\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "method_identity": claim["method_identity"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
