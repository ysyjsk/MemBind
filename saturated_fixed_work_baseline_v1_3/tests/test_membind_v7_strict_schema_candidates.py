from __future__ import annotations

from copy import deepcopy

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.provider_diagnostics import (
    build_minimal_json_schema_probe,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.strict_schema_candidates import (
    StrictSchemaCandidateError,
    require_strict_json_schema,
    select_strict_schema_model,
)


CANDIDATES = ["qwen3.5-plus-2026-04-20", "qwen3-max-2026-01-23"]
LANES = ["context-0-source-1", "context-1-source-2", "context-2-source-2"]


def test_strict_schema_transform_sets_strict_true_without_prompt_injection() -> None:
    probe = build_minimal_json_schema_probe(
        model=CANDIDATES[0],
        max_tokens=256,
        structured_output_mode="json_schema",
        send_max_tokens=False,
    )
    transformed = require_strict_json_schema(probe)

    response_format = transformed.request["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"]
    assert "max_tokens" not in transformed.request
    assert transformed.request["extra_body"] == {"enable_thinking": False}
    assert transformed.evidence["strict_json_schema"] is True
    assert transformed.evidence["prompt_schema_injection"] is False


def _model_result(model: str, *, fail_lane: str | None = None) -> dict[str, object]:
    lanes: list[dict[str, object]] = []
    for lane in LANES:
        repetitions = []
        for _ in range(2):
            status = "FAIL" if lane == fail_lane else "PASS"
            repetitions.append(
                {
                    "node": {"status": status},
                    "edge": {"status": status},
                }
            )
        lanes.append({"lane_id": lane, "repetitions": repetitions})
    return {"model": model, "available": True, "lanes": lanes}


def test_strict_selection_requires_every_lane_and_repetition() -> None:
    selection = select_strict_schema_model(
        candidates=CANDIDATES,
        lane_ids=LANES,
        results=[
            _model_result(CANDIDATES[0], fail_lane=LANES[1]),
            _model_result(CANDIDATES[1]),
        ],
        repetitions=2,
    )

    assert selection["status"] == "SELECTED"
    assert selection["selected_model"] == CANDIDATES[1]
    assert selection["eligible_models"] == [CANDIDATES[1]]
    assert selection["selection_rule"] == "FIRST_ALL_LANES_FULL_PASS_IN_FROZEN_ORDER"


def test_strict_selection_fails_closed_on_result_identity_drift() -> None:
    drifted = deepcopy(_model_result(CANDIDATES[0]))
    drifted["lanes"][0]["lane_id"] = "different"
    with pytest.raises(StrictSchemaCandidateError, match="identity"):
        select_strict_schema_model(
            candidates=CANDIDATES,
            lane_ids=LANES,
            results=[drifted, _model_result(CANDIDATES[1])],
            repetitions=2,
        )
