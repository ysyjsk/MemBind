"""Compatibility export for the shared MAB workload contract."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from mab_quality_v2_final_qa.workload_contract import *  # type: ignore[F403]
except ModuleNotFoundError:  # repository-local source checkout
    source = Path(__file__).resolve().parents[3] / "mab_quality_v2_final_qa" / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from mab_quality_v2_final_qa.workload_contract import *  # type: ignore[F403]
