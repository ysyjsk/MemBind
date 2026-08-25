"""Pure, fail-closed R3 Opportunity Gate A-E evaluator."""

from __future__ import annotations

import re
from typing import Any, Mapping


_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def evaluate_opportunity_gates(value: Mapping[str, Any]) -> dict[str, Any]:
    """Select exactly one legal method or fail closed to ``NULL``."""

    reasons: list[str] = []
    real = value.get("real_graphiti_evidence") is True
    blocks = value.get("independent_block_count")
    sources = value.get("source_count_per_block")
    manifest = value.get("sealed_manifest_sha256")
    evidence_complete = (
        real
        and value.get("observer_harness_bound") is True
        and blocks == 2
        and isinstance(sources, int)
        and not isinstance(sources, bool)
        and sources >= 6
        and isinstance(manifest, str)
        and bool(_DIGEST.fullmatch(manifest))
    )
    if not real:
        reasons.append("real Graphiti evidence is missing")
    if value.get("observer_harness_bound") is not True:
        reasons.append("observer harness source binding is missing")
    if blocks != 2 or not isinstance(sources, int) or isinstance(sources, bool) or sources < 6:
        reasons.append("two independent six-source blocks are required")
    if not isinstance(manifest, str) or not _DIGEST.fullmatch(manifest):
        reasons.append("sealed manifest digest is missing")

    false_stable = value.get("false_stable_count")
    false_unaffected = value.get("false_unaffected_count")
    stable_count = value.get("stable_prediction_count")
    gate_a = (
        evidence_complete
        and value.get("core_assumptions_supported") is True
        and value.get("t6b_status") == "SUPPORTED_WITH_GUARD"
        and false_stable == 0
        and false_unaffected == 0
        and isinstance(stable_count, int)
        and not isinstance(stable_count, bool)
        and stable_count > 0
    )
    if value.get("core_assumptions_supported") is not True:
        reasons.append("core assumptions are not supported")
    if false_stable != 0:
        reasons.append("false STABLE prediction observed")
    if false_unaffected != 0:
        reasons.append("false unaffected prediction observed")
    if not isinstance(stable_count, int) or isinstance(stable_count, bool) or stable_count <= 0:
        reasons.append("zero certifiable STABLE observations")
    if value.get("t6b_status") != "SUPPORTED_WITH_GUARD":
        reasons.append("guarded T6b refinement is not established")

    gate_b = gate_a and value.get("early_memory_specific") is True
    if value.get("early_memory_specific") is not True:
        reasons.append("early memory-specific validity is absent")

    csp = _number(value.get("csp"))
    csp_min = _number(value.get("csp_preregistered_min"))
    gross = _number(value.get("gross_saved_cp_lb_ns"))
    gate_c = (
        gate_b
        and csp is not None
        and csp_min is not None
        and csp >= csp_min
        and value.get("sca_within_bound") is True
        and value.get("meaningful_reconvergence") is True
        and gross is not None
        and gross > 0
    )
    if csp is None or csp_min is None or csp < csp_min:
        reasons.append("CSP is below its preregistered minimum")
    if value.get("sca_within_bound") is not True:
        reasons.append("semantic change amplification exceeds its bound")
    if value.get("meaningful_reconvergence") is not True:
        reasons.append("meaningful reconvergence is absent")
    if gross is None or gross <= 0:
        reasons.append("gross counterfactual saving is not positive")

    certificate = _number(value.get("certificate_cost_ub_ns"))
    repair = _number(value.get("repair_cost_ub_ns"))
    headroom = _number(value.get("required_online_headroom_ns"))
    margin = None if None in (gross, certificate, repair) else gross - certificate - repair
    gate_d = (
        gate_c
        and margin is not None
        and headroom is not None
        and min(certificate or 0, repair or 0, headroom) >= 0
        and margin > headroom
    )
    if not gate_d:
        reasons.append("offline opportunity margin does not exceed required headroom")

    selected = "NULL"
    if gate_d:
        if value.get("m1_sufficient") is True:
            selected = "M1"
        elif value.get("m2_extension_eligible") is True:
            selected = "M2"
        elif value.get("replay_allowed") is True:
            selected = "M0"
        else:
            reasons.append("no minimum sufficient method is eligible")
    gate_e = selected in {"M0", "M1", "M2"}
    authorized = all((gate_a, gate_b, gate_c, gate_d, gate_e))
    if not authorized:
        selected = "NULL"
    return {
        "schema_version": "membind.v7.method-selection.v2",
        "status": "AUTHORIZED" if authorized else "NULL",
        "authorized": authorized,
        "treatment_authorized": authorized,
        "selected_method": selected,
        "selected_operator": value.get("selected_operator") if authorized else None,
        "selected_seam": value.get("selected_seam") if authorized else None,
        "gates": {"A": gate_a, "B": gate_b, "C": gate_c, "D": gate_d, "E": gate_e},
        "offline_opportunity_margin_ns": margin,
        "reasons": list(dict.fromkeys(reasons)),
    }


__all__ = ["evaluate_opportunity_gates"]
