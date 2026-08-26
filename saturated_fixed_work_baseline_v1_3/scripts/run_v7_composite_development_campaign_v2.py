#!/usr/bin/env python3
"""Run V7 development replacement with content-free schema diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import run_v7_composite_development_campaign as base  # noqa: E402

from saturated_fixed_work_baseline_v1_3.membind_v7.development_provider_diagnostics import (  # noqa: E402
    augment_development_failure,
    install_development_schema_diagnostics,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.graphiti_observer import (  # noqa: E402
    current_provider_observation_scope,
)


PROTOCOL = base.PROJECT / "v7/BAILIAN_SILICONFLOW_V7_DEVELOPMENT_PROTOCOL_V2.json"
_ORIGINAL_SOURCE_BINDINGS = base._source_bindings
_ORIGINAL_RUNTIME_BUILDER = base.build_composite_engineering_runtime
_ORIGINAL_FAILURE_BUILDER = base.build_development_failure


def _source_bindings() -> dict[str, str]:
    result = _ORIGINAL_SOURCE_BINDINGS()
    additions = {
        "saturated_fixed_work_baseline_v1_3/scripts/run_v7_composite_development_campaign_v2.py": (
            Path(__file__).resolve()
        ),
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/development_provider_diagnostics.py": (
            base.PROJECT
            / "src/saturated_fixed_work_baseline_v1_3/membind_v7/development_provider_diagnostics.py"
        ),
    }
    result.update({name: base._sha256(path) for name, path in additions.items()})
    return dict(sorted(result.items()))


def _build_runtime(**kwargs: Any) -> Any:
    runtime = _ORIGINAL_RUNTIME_BUILDER(**kwargs)
    install_development_schema_diagnostics(
        runtime.validated_llm,
        scope_reader=current_provider_observation_scope,
    )
    return runtime


def _build_failure(**kwargs: Any) -> dict[str, Any]:
    error = kwargs["error"]
    return augment_development_failure(
        _ORIGINAL_FAILURE_BUILDER(**kwargs),
        error,
    )


base.PROTOCOL = PROTOCOL
base._source_bindings = _source_bindings
base.build_composite_engineering_runtime = _build_runtime
base.build_development_failure = _build_failure


if __name__ == "__main__":
    raise SystemExit(base.main())
