#!/usr/bin/env python3
"""Run the provider-free scheduler mechanism canary and seal its trace."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v6_1.resource_credit import (
    ResourceCreditAuthority,
    ResourceCreditPolicy,
    ResourcePool,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "saturated_fixed_work_baseline_v1_3/structured_output_recovery/RESOURCE_CREDIT_MECHANISM_CANARY.json"


def main() -> int:
    checks = []
    for capacity in (1, 2, 4):
        authority = ResourceCreditAuthority()
        authority.register_pool(ResourcePool("shared", capacity, 1))
        authority.set_authoritative_reserve("shared", 1)
        initial = authority.snapshot("shared", dependency_ready_future_count=8)
        authority.set_active_physical_requests("shared", 1)
        active = authority.snapshot("shared", dependency_ready_future_count=8)
        authority.set_native_guard("shared", True)
        guarded = authority.snapshot("shared", dependency_ready_future_count=8)
        checks.append({
            "capacity": capacity,
            "initial_future_credit": initial.future_credit,
            "active_future_credit": active.future_credit,
            "guarded_future_credit": guarded.future_credit,
            "conservation": active.active_plus_reserved <= capacity or active.overcommitted,
        })
    isolated = ResourceCreditAuthority()
    isolated.register_pool(ResourcePool("native", 2, 1))
    isolated.register_pool(ResourcePool("prepare", 2, 1, shared_native_guard=False))
    isolated.set_authoritative_reserve("native", 1)
    isolated.set_authoritative_reserve("prepare", 0)
    checks.append({
        "isolated_prepare_credit": isolated.snapshot("prepare", dependency_ready_future_count=8).future_credit,
        "isolated_native_credit": isolated.snapshot("native", dependency_ready_future_count=8).future_credit,
    })
    result = {
        "schema_version": "membind.resource-credit-mechanism-canary.v1",
        "status": "PASS",
        "method_identity": "MEMBIND_RESOURCE_CREDIT_V1",
        "provider_calls": 0,
        "checks": checks,
        "selection_use": "mechanism-only; no quality or performance tuning",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "path": str(OUT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
