"""Minimal reducer for construction records."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def summarize(path: str | Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    by_arm: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("status") == "PASS" and isinstance(row.get("makespan_ns"), (int, float)):
            by_arm[str(row.get("arm"))].append(float(row["makespan_ns"]))
    return {arm: {"count": len(values), "mean_makespan_ns": sum(values) / len(values)} for arm, values in sorted(by_arm.items())}

