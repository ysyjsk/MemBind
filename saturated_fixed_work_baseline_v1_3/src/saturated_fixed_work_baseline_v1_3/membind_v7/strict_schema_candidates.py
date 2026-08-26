"""Strict provider-native JSON-schema candidate contracts and reducer."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .provider_diagnostics import StructuredExtractionProbe


class StrictSchemaCandidateError(ValueError):
    pass


def require_strict_json_schema(
    probe: StructuredExtractionProbe,
) -> StructuredExtractionProbe:
    """Return a probe that requires provider-native strict JSON schema."""

    if not isinstance(probe, StructuredExtractionProbe):
        raise StrictSchemaCandidateError("strict schema probe identity is invalid")
    request = deepcopy(probe.request)
    response_format = request.get("response_format")
    wrapper = (
        response_format.get("json_schema")
        if isinstance(response_format, Mapping)
        else None
    )
    schema = wrapper.get("schema") if isinstance(wrapper, Mapping) else None
    name = wrapper.get("name") if isinstance(wrapper, Mapping) else None
    if (
        not isinstance(response_format, Mapping)
        or response_format.get("type") != "json_schema"
        or not isinstance(wrapper, Mapping)
        or not isinstance(schema, Mapping)
        or not schema
        or not isinstance(name, str)
        or not name
    ):
        raise StrictSchemaCandidateError("strict schema probe response format is invalid")
    request["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": deepcopy(dict(schema)),
        },
    }
    request.pop("max_tokens", None)
    extra = request.get("extra_body")
    selected_extra = dict(extra) if isinstance(extra, Mapping) else {}
    if selected_extra.get("enable_thinking") not in {None, False}:
        raise StrictSchemaCandidateError("strict schema thinking policy drifted")
    selected_extra["enable_thinking"] = False
    request["extra_body"] = selected_extra
    evidence = deepcopy(probe.evidence)
    evidence.update(
        {
            "structured_output_mode": "json_schema",
            "strict_json_schema": True,
            "prompt_schema_injection": False,
            "max_tokens_sent": False,
        }
    )
    return StructuredExtractionProbe(
        request=request,
        response_model=probe.response_model,
        evidence=evidence,
        probe_kind=probe.probe_kind,
        result_field=probe.result_field,
    )


def _full_pass(
    value: Mapping[str, Any], *, lane_ids: Sequence[str], repetitions: int
) -> bool:
    if value.get("available") is not True:
        return False
    lanes = value.get("lanes")
    if not isinstance(lanes, list) or len(lanes) != len(lane_ids):
        return False
    for expected_lane, lane in zip(lane_ids, lanes, strict=True):
        if not isinstance(lane, Mapping) or lane.get("lane_id") != expected_lane:
            return False
        rows = lane.get("repetitions")
        if not isinstance(rows, list) or len(rows) != repetitions:
            return False
        if not all(
            isinstance(row, Mapping)
            and isinstance(row.get("node"), Mapping)
            and isinstance(row.get("edge"), Mapping)
            and row["node"].get("status") == "PASS"
            and row["edge"].get("status") == "PASS"
            for row in rows
        ):
            return False
    return True


def select_strict_schema_model(
    *,
    candidates: Sequence[str],
    lane_ids: Sequence[str],
    results: Sequence[Mapping[str, Any]],
    repetitions: int,
) -> dict[str, Any]:
    """Select the first candidate that passes every frozen lane and repeat."""

    selected_candidates = list(candidates)
    selected_lanes = list(lane_ids)
    identity_valid = (
        bool(selected_candidates)
        and len(set(selected_candidates)) == len(selected_candidates)
        and all(isinstance(model, str) and model for model in selected_candidates)
        and bool(selected_lanes)
        and len(set(selected_lanes)) == len(selected_lanes)
        and all(isinstance(lane, str) and lane for lane in selected_lanes)
        and isinstance(repetitions, int)
        and not isinstance(repetitions, bool)
        and repetitions > 0
        and len(results) == len(selected_candidates)
        and all(
            isinstance(result, Mapping) and result.get("model") == model
            for model, result in zip(selected_candidates, results, strict=True)
        )
    )
    if not identity_valid:
        raise StrictSchemaCandidateError("strict schema candidate identity drifted")
    for result in results:
        lanes = result.get("lanes")
        if not isinstance(lanes, list) or [
            lane.get("lane_id") if isinstance(lane, Mapping) else None
            for lane in lanes
        ] != selected_lanes:
            raise StrictSchemaCandidateError("strict schema lane identity drifted")
    eligible = [
        model
        for model, result in zip(selected_candidates, results, strict=True)
        if _full_pass(result, lane_ids=selected_lanes, repetitions=repetitions)
    ]
    return {
        "schema_version": "membind.v7.strict-schema-model-selection.v1",
        "status": "SELECTED" if eligible else "NO_ELIGIBLE_MODEL",
        "selected_model": eligible[0] if eligible else None,
        "eligible_models": eligible,
        "selection_rule": "FIRST_ALL_LANES_FULL_PASS_IN_FROZEN_ORDER",
        "structured_output_mode": "json_schema",
        "strict_json_schema": True,
        "lane_ids": selected_lanes,
        "repetitions": repetitions,
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
    }


__all__ = [
    "StrictSchemaCandidateError",
    "require_strict_json_schema",
    "select_strict_schema_model",
]
