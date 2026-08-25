"""Compatibility export for the pinned MAB main dataset adapter."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from mab_quality_v2_final_qa.mab_main_dataset import *  # type: ignore[F403]
except ModuleNotFoundError:  # repository-local source checkout
    source = Path(__file__).resolve().parents[3] / "mab_quality_v2_final_qa" / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from mab_quality_v2_final_qa.mab_main_dataset import *  # type: ignore[F403]
