"""Real-observer R2/R3 pair analysis and conservative Gate A-E inputs."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Mapping, Sequence

from .analysis import confusion_matrix, false_stable_rate
from .certificates import CertificateResult, CertificateStatus, Witness, certify_exact_topk
from .gates import evaluate_opportunity_gates
from .graphiti_observer import canonical_digest, exact_cosine_domain
from .opportunity import DagNode, costed_counterfactual, counterfactual, longest_path
from .state_delta import StateDelta


class CharacterizationError(RuntimeError):
    pass


_DEPENDENCY_KINDS = {
    "data",
    "control",
    "existence",
    "ordered-collection",
    "environment/oracle",
    "effect/publication",
}

_PREVIOUS_EPISODE_DIRECT_CONSUMERS = {
    "node_extraction",
    "node_resolution",
    "edge_extraction",
    "edge_resolution",
    "attributes_summary",
}


def _duration(value: Mapping[str, Any], start: str, end: str) -> int:
    left = value.get(start)
    right = value.get(end)
    if any(isinstance(item, bool) or not isinstance(item, int) for item in (left, right)):
        return 0
    return max(0, int(right) - int(left))


def _read_key(value: Mapping[str, Any]) -> tuple[str, int]:
    operator = value.get("operator")
    occurrence = value.get("occurrence")
    if not isinstance(operator, str) or isinstance(occurrence, bool) or not isinstance(occurrence, int):
        raise CharacterizationError("read stable name is invalid")
    return operator, occurrence


def _request_key(value: Mapping[str, Any]) -> tuple[str, int]:
    prompt = value.get("prompt_name")
    ordinal = value.get("ordinal")
    if not isinstance(prompt, str) or isinstance(ordinal, bool) or not isinstance(ordinal, int):
        raise CharacterizationError("request stable name is invalid")
    return prompt, ordinal


def _unique(values: Sequence[Mapping[str, Any]], key_fn: Any, *, label: str) -> dict[Any, Mapping[str, Any]]:
    result: dict[Any, Mapping[str, Any]] = {}
    for value in values:
        key = key_fn(value)
        if key in result:
            raise CharacterizationError(f"ambiguous {label} stable name")
        result[key] = value
    return result


def _post_scores(old: Mapping[str, Any], delta: StateDelta) -> dict[str, float]:
    result: dict[str, float] = {}
    query = old.get("query")
    if not isinstance(query, Sequence) or isinstance(query, (str, bytes, bytearray)):
        return result
    for change in delta.changes:
        if change.kind != "node" or str(change.operation).casefold() in {"delete", "remove"}:
            continue
        embedding = change.after.get("name_embedding") if isinstance(change.after, Mapping) else None
        if not isinstance(embedding, Sequence) or isinstance(embedding, (str, bytes, bytearray)):
            continue
        try:
            scored = exact_cosine_domain(
                query=query,
                domain={change.key: embedding},
                limit=1,
                min_score=-2.0,
            )
        except Exception:
            continue
        result[change.key] = float(scored["domain"][0]["score"])
    return result


def _certificate(old: Mapping[str, Any], fresh: Mapping[str, Any], delta: StateDelta) -> CertificateResult:
    identity_fields = (
        "operator",
        "query",
        "filter_fingerprint",
        "group_ids",
        "limit",
        "min_score",
        "query_epoch",
        "index_epoch",
        "config_epoch",
    )
    if any(canonical_digest(old.get(field)) != canonical_digest(fresh.get(field)) for field in identity_fields):
        return CertificateResult(CertificateStatus.UNKNOWN, "read query/filter/config identity changed")
    if old.get("completeness_status") != "COMPLETE" or fresh.get("completeness_status") != "COMPLETE":
        return CertificateResult(CertificateStatus.UNKNOWN, "read observation is incomplete")
    domain_rows = old.get("complete_domain")
    if not isinstance(domain_rows, Sequence):
        return CertificateResult(CertificateStatus.UNKNOWN, "old complete domain is missing")
    domain = tuple(
        str(row.get("uuid"))
        for row in domain_rows
        if isinstance(row, Mapping) and isinstance(row.get("uuid"), str)
    )
    actual = old.get("actual_result")
    if not isinstance(actual, Sequence) or isinstance(actual, (str, bytes, bytearray)):
        return CertificateResult(CertificateStatus.UNKNOWN, "old ordered result is missing")
    ties = old.get("boundary_ties")
    if not isinstance(ties, Sequence) or isinstance(ties, (str, bytes, bytearray)):
        ties = ()
    post_scores = _post_scores(old, delta)
    changed_nonmembers = [
        change
        for change in delta.changes
        if change.kind == "node"
        and change.key not in set(str(item) for item in actual)
        and str(change.operation).casefold() not in {"delete", "remove"}
    ]
    min_score = old.get("min_score")
    no_new_eligible = (
        isinstance(min_score, (int, float))
        and not isinstance(min_score, bool)
        and all(
            change.key in post_scores and post_scores[change.key] <= float(min_score)
            for change in changed_nonmembers
        )
    )
    witness = Witness(
        operator=str(old.get("operator")),
        query=tuple(float(item) for item in old.get("query", ())),
        result=tuple(str(item) for item in actual),
        domain=domain,
        k=int(old.get("limit")),
        cutoff=float(old["cutoff"]) if old.get("cutoff") is not None else None,
        ties=tuple(str(item) for item in ties),
        query_epoch=str(old.get("query_epoch") or ""),
        index_epoch=str(old.get("index_epoch") or ""),
        filter_fingerprint=str(old.get("filter_fingerprint") or ""),
        proof_data={
            "post_scores": post_scores,
            "tie_contract": "strict-score-separation" if not ties else None,
            "no_new_eligible": no_new_eligible,
            "min_score": min_score,
            "required_epochs": (
                "query_epoch",
                "index_epoch",
                "embedder_epoch",
                "config_epoch",
                "backend_epoch",
            ),
        },
    )
    return certify_exact_topk(witness, delta)


def _truth(old: Mapping[str, Any], fresh: Mapping[str, Any]) -> str:
    fields = ("operator", "query", "filter_fingerprint", "group_ids", "limit", "min_score", "actual_result")
    return (
        "SAME"
        if all(canonical_digest(old.get(field)) == canonical_digest(fresh.get(field)) for field in fields)
        else "CHANGED"
    )


def _request_truth(old: Sequence[Mapping[str, Any]], fresh: Sequence[Mapping[str, Any]]) -> str:
    old_by_key = _unique(old, _request_key, label="request")
    fresh_by_key = _unique(fresh, _request_key, label="request")
    if set(old_by_key) != set(fresh_by_key):
        return "CHANGED"
    return (
        "SAME"
        if all(
            old_by_key[key].get("request_identity") == fresh_by_key[key].get("request_identity")
            for key in old_by_key
        )
        else "CHANGED"
    )


def _reconvergence(old: Sequence[Mapping[str, Any]], fresh: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    left = [(value.get("prompt_name"), value.get("request_identity")) for value in old]
    right = [(value.get("prompt_name"), value.get("request_identity")) for value in fresh]
    if len(left) != len(right):
        return {"exact_suffix_count": 0, "rate": 0.0, "first_suffix_index": None}
    first: int | None = None
    for index in range(len(left) + 1):
        if left[index:] == right[index:]:
            first = index
            break
    suffix = 0 if first is None else len(left) - first
    return {
        "exact_suffix_count": suffix,
        "rate": 1.0 if not left else suffix / len(left),
        "first_suffix_index": first,
    }


def analyze_build_pair(old: Mapping[str, Any], fresh: Mapping[str, Any], delta: StateDelta) -> dict[str, Any]:
    if old.get("phase") != "OLD" or fresh.get("phase") != "FRESH_NATIVE":
        raise CharacterizationError("build pair phases are invalid")
    if old.get("source_sequence") != fresh.get("source_sequence"):
        raise CharacterizationError("build pair source identity differs")
    old_reads = _unique(list(old.get("reads") or ()), _read_key, label="read")
    fresh_reads = _unique(list(fresh.get("reads") or ()), _read_key, label="read")
    rows: list[dict[str, Any]] = []
    for key in sorted(set(old_reads) | set(fresh_reads)):
        if key not in old_reads or key not in fresh_reads:
            prediction = CertificateStatus.UNKNOWN
            reason = "read alignment side is missing"
            truth = "CHANGED"
            native_duration = 0
            observer_overhead = 0
        else:
            old_read = old_reads[key]
            fresh_read = fresh_reads[key]
            certificate = _certificate(old_read, fresh_read, delta)
            prediction = certificate.status
            reason = certificate.reason
            truth = _truth(old_read, fresh_read)
            native_duration = _duration(fresh_read, "native_start_ns", "native_end_ns")
            total_observer = _duration(old_read, "observer_start_ns", "observer_end_ns")
            old_native = _duration(old_read, "native_start_ns", "native_end_ns")
            observer_overhead = max(0, total_observer - old_native)
        rows.append(
            {
                "operator": key[0],
                "occurrence": key[1],
                "prediction": prediction.value,
                "truth": truth,
                "reason": reason,
                "certificate_inputs": ["old_witness", "state_delta", "changed_node_post_scores"],
                "native_duration_ns": native_duration,
                "certificate_cost_ub_ns": observer_overhead,
            }
        )
    false_stable = sum(row["prediction"] == "STABLE" and row["truth"] == "CHANGED" for row in rows)
    old_requests = list(old.get("requests") or ())
    fresh_requests = list(fresh.get("requests") or ())
    request_truth = _request_truth(old_requests, fresh_requests)
    old_previous = (old.get("previous_episode") or {}).get("projection_digest")
    fresh_previous = (fresh.get("previous_episode") or {}).get("projection_digest")
    previous_same = bool(old_previous) and old_previous == fresh_previous
    read_stable = bool(rows) and all(row["prediction"] == "STABLE" for row in rows)
    demand_reasons: list[str] = []
    if not previous_same:
        demand_reasons.append("previous_episode")
    if not read_stable:
        demand_reasons.append("semantic_read")
    if old.get("continuation", {}).get("status") != "SUPPORTED_WITH_GUARD":
        demand_reasons.append("continuation_guard")
    demand_prediction = "STABLE" if not demand_reasons else "UNKNOWN"
    demand_truth = "SAME" if request_truth == "SAME" else "CHANGED"
    false_unaffected = int(demand_prediction == "STABLE" and demand_truth == "CHANGED")
    reconvergence = _reconvergence(old_requests, fresh_requests)
    stable_read_work = sum(row["native_duration_ns"] for row in rows if row["prediction"] == "STABLE")
    total_state_work = max(0, int(fresh.get("duration_ns") or 0))
    return {
        "schema_version": "membind.v7.r3-build-pair-analysis.v1",
        "source_sequence": old.get("source_sequence"),
        "read_rows": rows,
        "request_truth": request_truth,
        "demand_prediction": demand_prediction,
        "demand_truth": demand_truth,
        "demand_reasons": demand_reasons,
        "early_memory_specific": demand_prediction == "STABLE" and bool(rows),
        "false_stable_count": false_stable,
        "false_unaffected_count": false_unaffected,
        "reconvergence": reconvergence,
        "certifiable_work_ns": stable_read_work if demand_prediction == "STABLE" else 0,
        "state_dependent_work_ns": total_state_work,
    }


def _threshold(value: Mapping[str, Any], name: str) -> float:
    result = value.get(name)
    if isinstance(result, bool) or not isinstance(result, (int, float)) or float(result) < 0:
        raise CharacterizationError(f"R3 threshold is invalid: {name}")
    return float(result)


def _dag_node(value: Mapping[str, Any], *, label: str) -> DagNode:
    node_id = value.get("node_id")
    predecessors = value.get("predecessors")
    cost = value.get("cost_ns")
    if (
        not isinstance(node_id, str)
        or not node_id
        or not isinstance(predecessors, Sequence)
        or isinstance(predecessors, (str, bytes, bytearray))
        or any(not isinstance(parent, str) or not parent for parent in predecessors)
        or isinstance(cost, bool)
        or not isinstance(cost, int)
        or cost < 0
    ):
        raise CharacterizationError(f"{label} semantic DAG node is invalid")
    return DagNode(node_id, tuple(predecessors), float(cost))


def _semantic_dag_metrics(
    pair: Mapping[str, Any], analysis: Mapping[str, Any]
) -> dict[str, Any] | None:
    value = pair.get("semantic_dag")
    if not isinstance(value, Mapping) or value.get("status") != "COMPLETE":
        return None
    if value.get("schema_version") != "membind.v7.semantic-cost-dag.v1":
        raise CharacterizationError("semantic DAG schema is invalid")
    raw_nodes = value.get("nodes")
    raw_cost_nodes = value.get("cost_nodes")
    if (
        not isinstance(raw_nodes, Sequence)
        or isinstance(raw_nodes, (str, bytes, bytearray))
        or not raw_nodes
        or not isinstance(raw_cost_nodes, Sequence)
        or isinstance(raw_cost_nodes, (str, bytes, bytearray))
        or any(not isinstance(row, Mapping) for row in (*raw_nodes, *raw_cost_nodes))
    ):
        raise CharacterizationError("semantic DAG rows are invalid")
    nodes = tuple(_dag_node(row, label="baseline") for row in raw_nodes)
    node_rows = {node.node_id: row for node, row in zip(nodes, raw_nodes, strict=True)}
    if len(node_rows) != len(nodes):
        raise CharacterizationError("semantic DAG node IDs are ambiguous")
    node_ids = set(node_rows)
    if any(not set(node.predecessors) <= node_ids for node in nodes):
        raise CharacterizationError("semantic DAG references an unknown baseline node")
    try:
        baseline = longest_path(nodes)
    except (KeyError, ValueError) as exc:
        raise CharacterizationError("semantic DAG is not a finite DAG") from exc

    read_predictions = {
        (str(row["operator"]), int(row["occurrence"])): str(row["prediction"])
        for row in analysis.get("read_rows", ())
    }
    mapped_read_keys: set[tuple[str, int]] = set()
    removable: set[str] = set()
    for node_id, row in node_rows.items():
        raw_key = row.get("read_key")
        if raw_key is None:
            continue
        if (
            not isinstance(raw_key, Sequence)
            or isinstance(raw_key, (str, bytes, bytearray))
            or len(raw_key) != 2
            or not isinstance(raw_key[0], str)
            or isinstance(raw_key[1], bool)
            or not isinstance(raw_key[1], int)
        ):
            raise CharacterizationError("semantic DAG read binding is invalid")
        key = (raw_key[0], raw_key[1])
        if key not in read_predictions or key in mapped_read_keys:
            raise CharacterizationError("semantic DAG read binding is incomplete or ambiguous")
        mapped_read_keys.add(key)
        if (
            read_predictions[key] == "STABLE"
            and analysis.get("demand_prediction") == "STABLE"
        ):
            removable.add(node_id)
    stable_keys = {key for key, status in read_predictions.items() if status == "STABLE"}
    if not stable_keys <= mapped_read_keys:
        raise CharacterizationError("semantic DAG omits a certifiable read")

    try:
        gross = counterfactual(nodes, removed=removable)
    except ValueError as exc:
        raise CharacterizationError("gross semantic DAG counterfactual is invalid") from exc

    additions: list[DagNode] = []
    gates: dict[str, tuple[str, ...]] = {}
    certificate_cost = 0
    repair_cost = 0
    for raw in raw_cost_nodes:
        assert isinstance(raw, Mapping)
        node = _dag_node(raw, label="costed")
        kind = raw.get("kind")
        targets = raw.get("gates")
        if kind not in {"certificate", "repair"} or (
            not isinstance(targets, Sequence)
            or isinstance(targets, (str, bytes, bytearray))
            or any(not isinstance(target, str) or target not in node_ids for target in targets)
        ):
            raise CharacterizationError("semantic DAG cost binding is invalid")
        additions.append(node)
        for target in targets:
            gates[target] = tuple((*gates.get(target, ()), node.node_id))
        if kind == "certificate":
            certificate_cost += int(node.cost)
        else:
            repair_cost += int(node.cost)
    try:
        costed = costed_counterfactual(
            nodes,
            removed=removable,
            added=additions,
            gates=gates,
        )
    except ValueError as exc:
        raise CharacterizationError("costed semantic DAG counterfactual is invalid") from exc

    baseline_path = set(baseline.path)
    state_cp = sum(
        int(node.cost)
        for node in nodes
        if node.node_id in baseline_path and node_rows[node.node_id].get("state_dependent") is True
    )
    certifiable_cp = sum(
        int(node.cost)
        for node in nodes
        if node.node_id in baseline_path and node.node_id in removable
    )
    return {
        "source_sequence": analysis.get("source_sequence"),
        "baseline_cp_ns": int(baseline.cost),
        "baseline_path": list(baseline.path),
        "gross_candidate_cp_ns": int(gross.candidate.cost),
        "gross_saved_cp_ns": int(gross.saved_cost),
        "gross_path": list(gross.candidate.path),
        "costed_candidate_cp_ns": int(costed.candidate.cost),
        "costed_saved_cp_ns": int(costed.saved_cost),
        "costed_path": list(costed.candidate.path),
        "state_dependent_cp_ns": state_cp,
        "certifiable_cp_ns": certifiable_cp,
        "certificate_cost_ub_ns": certificate_cost,
        "repair_cost_ub_ns": repair_cost,
        "removable_node_ids": sorted(removable),
    }


def characterize_r3_blocks(
    blocks: Sequence[Mapping[str, Any]],
    *,
    thresholds: Mapping[str, Any],
    sealed_manifest_sha256: str = "0" * 64,
) -> dict[str, Any]:
    if len(blocks) != 2 or len({str(block.get("block_id")) for block in blocks}) != 2:
        raise CharacterizationError("R3 requires two distinct blocks")
    csp_min = _threshold(thresholds, "csp_min")
    sca_max = _threshold(thresholds, "sca_work_max")
    reconvergence_min = _threshold(thresholds, "reconvergence_min")
    headroom_floor = _threshold(thresholds, "required_headroom_floor_ns")
    headroom_ratio = _threshold(thresholds, "required_headroom_ratio")
    analyses: list[dict[str, Any]] = []
    per_block: list[list[dict[str, Any]]] = []
    dag_per_block: list[list[dict[str, Any] | None]] = []
    for block in blocks:
        pairs = list(block.get("pairs") or ())
        block_rows = [
            analyze_build_pair(pair["old_build"], pair["fresh_build"], pair["delta"])
            for pair in pairs
        ]
        if not block_rows:
            raise CharacterizationError("R3 block has no old/fresh pairs")
        per_block.append(block_rows)
        dag_per_block.append(
            [
                _semantic_dag_metrics(pair, analysis)
                for pair, analysis in zip(pairs, block_rows, strict=True)
            ]
        )
        analyses.extend(block_rows)
    read_rows = [row for analysis in analyses for row in analysis["read_rows"]]
    predictions = [row["prediction"] for row in read_rows]
    truths = [row["truth"] for row in read_rows]
    matrix = confusion_matrix(predictions, truths)
    false_stable = sum(analysis["false_stable_count"] for analysis in analyses)
    false_unaffected = sum(analysis["false_unaffected_count"] for analysis in analyses)
    stable_count = sum(row["prediction"] == "STABLE" for row in read_rows)
    semantic_dag_complete = all(
        metric is not None for block_metrics in dag_per_block for metric in block_metrics
    )
    dag_metrics = [
        metric
        for block_metrics in dag_per_block
        for metric in block_metrics
        if metric is not None
    ]
    certifiable = sum(int(analysis["certifiable_work_ns"]) for analysis in analyses)
    state_work = sum(int(analysis["state_dependent_work_ns"]) for analysis in analyses)
    state_cp = sum(int(metric["state_dependent_cp_ns"]) for metric in dag_metrics)
    certifiable_cp = sum(int(metric["certifiable_cp_ns"]) for metric in dag_metrics)
    csp = None if state_cp == 0 or not semantic_dag_complete else certifiable_cp / state_cp
    direct_work = sum(
        int(row["native_duration_ns"])
        for row in read_rows
        if row["prediction"] in {"INVALID", "UNKNOWN"}
    )
    affected_work = sum(
        int(analysis["state_dependent_work_ns"])
        for analysis in analyses
        if analysis["demand_prediction"] != "STABLE"
    )
    sca = None if direct_work == 0 else affected_work / direct_work
    absolute_cascade_without_direct_root = direct_work == 0 and affected_work > 0
    reconvergence_rate = sum(float(analysis["reconvergence"]["rate"]) for analysis in analyses) / len(analyses)
    early = bool(analyses) and all(analysis["early_memory_specific"] for analysis in analyses)
    block_gross = [
        sum(int(metric["gross_saved_cp_ns"]) for metric in block_metrics if metric is not None)
        for block_metrics in dag_per_block
    ]
    block_costed = [
        sum(int(metric["costed_saved_cp_ns"]) for metric in block_metrics if metric is not None)
        for block_metrics in dag_per_block
    ]
    gross_lb = min(block_gross) if semantic_dag_complete else None
    costed_lb = min(block_costed) if semantic_dag_complete else None
    certificate_ub = (
        max(
            sum(int(metric["certificate_cost_ub_ns"]) for metric in block_metrics if metric is not None)
            for block_metrics in dag_per_block
        )
        if semantic_dag_complete
        else 0
    )
    repair_ub = (
        max(
            sum(int(metric["repair_cost_ub_ns"]) for metric in block_metrics if metric is not None)
            for block_metrics in dag_per_block
        )
        if semantic_dag_complete
        else 0
    )
    headroom = max(headroom_floor, headroom_ratio * state_work)
    t6b = all(
        pair[side].get("continuation", {}).get("status") == "SUPPORTED_WITH_GUARD"
        for block in blocks
        for pair in list(block.get("pairs") or ())
        for side in ("old_build", "fresh_build")
    )
    decision_input = {
        "schema_version": "membind.v7.r3-decision-input.v1",
        "real_graphiti_evidence": all(block.get("real_graphiti_evidence") is True for block in blocks),
        "independent_block_count": 2,
        "source_count_per_block": min(int(block.get("source_count") or 0) for block in blocks),
        "selected_operator": "node_cosine",
        "selected_seam": "graphiti.add_episode.pre_process_episode_data",
        "t6b_status": "SUPPORTED_WITH_GUARD" if t6b else "UNKNOWN",
        "core_assumptions_supported": True,
        "false_stable_count": false_stable,
        "false_unaffected_count": false_unaffected,
        "stable_prediction_count": stable_count,
        "early_memory_specific": early,
        "csp": csp,
        "csp_preregistered_min": csp_min,
        "sca_work": sca,
        "sca_within_bound": sca is not None and sca <= sca_max and not absolute_cascade_without_direct_root,
        "meaningful_reconvergence": reconvergence_rate >= reconvergence_min,
        "reconvergence_rate": reconvergence_rate,
        "gross_saved_cp_lb_ns": gross_lb,
        "certificate_cost_ub_ns": certificate_ub,
        "repair_cost_ub_ns": repair_ub,
        "required_online_headroom_ns": headroom,
        "m1_sufficient": stable_count > 0,
        "m2_extension_eligible": False,
        "replay_allowed": False,
        "sealed_manifest_sha256": sealed_manifest_sha256,
    }
    method = evaluate_opportunity_gates(decision_input)
    return {
        "schema_version": "membind.v7.r3-characterization.v1",
        "pair_analyses": analyses,
        "certificate_confusion": matrix,
        "false_stable_rate": false_stable_rate(matrix),
        "false_unaffected_count": false_unaffected,
        "csp": csp,
        "semantic_change_amplification": {
            "direct_work_ns": direct_work,
            "affected_work_ns": affected_work,
            "sca_work": sca,
            "absolute_cascade_without_direct_root": absolute_cascade_without_direct_root,
        },
        "reconvergence": {"mean_rate": reconvergence_rate},
        "critical_opportunity": {
            "status": (
                "COMPLETE"
                if semantic_dag_complete
                else "UNKNOWN_INCOMPLETE_SEMANTIC_DAG"
            ),
            "gross_saved_cp_lb_ns": gross_lb,
            "costed_saved_cp_lb_ns": costed_lb,
            "certificate_cost_ub_ns": certificate_ub,
            "repair_cost_ub_ns": repair_ub,
            "required_online_headroom_ns": headroom,
            "pair_dag_metrics": dag_metrics,
        },
        "decision_input": decision_input,
        "method_selection": method,
    }


def audit_r1_assumptions(block: Mapping[str, Any]) -> dict[str, Any]:
    if block.get("real_graphiti_evidence") is not True:
        raise CharacterizationError("R1 requires real Graphiti evidence")
    transitions = list(block.get("transitions") or ())
    pairs = list(block.get("pairs") or ())
    if not transitions or not pairs:
        raise CharacterizationError("R1 block evidence is incomplete")
    builds = [
        pair[side]
        for pair in pairs
        for side in ("old_build", "fresh_build")
    ]
    all_dependency_kinds = {
        str(edge.get("kind"))
        for build in builds
        for edge in list(build.get("dependency_edges") or ())
        if isinstance(edge, Mapping)
    }
    dependency_pairs = {
        (str(edge.get("source")), str(edge.get("target")))
        for build in builds
        for edge in list(build.get("dependency_edges") or ())
        if isinstance(edge, Mapping)
    }
    previous_episode_dependency_complete = all(
        ("previous_episode", target) in dependency_pairs
        for target in _PREVIOUS_EPISODE_DIRECT_CONSUMERS
    )
    reads = [read for build in builds for read in list(build.get("reads") or ())]
    requests = [request for build in builds for request in list(build.get("requests") or ())]
    deltas = [transition.get("delta") for transition in transitions]
    exact_delta_images = all(
        isinstance(delta, StateDelta)
        and all(
            change.operation in {"insert", "update", "delete"}
            and isinstance(change.before, Mapping)
            and isinstance(change.after, Mapping)
            for change in delta.changes
        )
        for delta in deltas
    )
    stable_names_unique = True
    for build in builds:
        try:
            _unique(list(build.get("reads") or ()), _read_key, label="read")
            _unique(list(build.get("requests") or ()), _request_key, label="request")
        except CharacterizationError:
            stable_names_unique = False
    statuses = {
        "A1": "SUPPORTED_WITH_GUARD",
        "A2": "SUPPORTED_WITH_GUARD",
        "A3": "SUPPORTED" if exact_delta_images else "UNKNOWN",
        "A4": (
            "SUPPORTED"
            if _DEPENDENCY_KINDS <= all_dependency_kinds
            and previous_episode_dependency_complete
            else "UNKNOWN"
        ),
        "A5": "SUPPORTED" if stable_names_unique else "UNKNOWN",
        "A6": "SUPPORTED_WITH_GUARD",
        "A7": "SUPPORTED_WITH_GUARD" if all(read.get("completeness_status") == "COMPLETE" for read in reads) else "UNKNOWN",
        "A8": "SUPPORTED" if reads and all(read.get("witness") for read in reads) else "UNKNOWN",
        "A9": "UNKNOWN",
        "A10": "SUPPORTED" if requests and all(request.get("request_identity") for request in requests) else "UNKNOWN",
        "A15": "SUPPORTED_BY_REFERENCE_MODEL",
        "A16": "SUPPORTED_WITH_GUARD" if all(build.get("continuation", {}).get("status") == "SUPPORTED_WITH_GUARD" for build in builds) else "UNKNOWN",
    }
    core = {"A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A10", "A15", "A16"}
    supported = all(str(statuses[name]).startswith("SUPPORTED") for name in core)
    return {
        "schema_version": "membind.v7.r1-assumption-audit.v2",
        "status": "PASS_WITH_GUARDS" if supported else "FAIL_CLOSED",
        "real_graphiti_evidence": True,
        "assumptions": statuses,
        "core_assumptions_supported": supported,
        "dependency_edge_kinds_observed": sorted(all_dependency_kinds),
        "dependency_edge_kinds_complete": _DEPENDENCY_KINDS <= all_dependency_kinds,
        "previous_episode_dependency_complete": previous_episode_dependency_complete,
        "read_observation_count": len(reads),
        "request_observation_count": len(requests),
        "replay_allowed": False,
        "replay_contract_status": "UNKNOWN",
        "treatment_calls": 0,
        "shadow_publication_calls": block.get("shadow_publication_calls"),
    }


def build_r2_causal_trace(block: Mapping[str, Any]) -> dict[str, Any]:
    pairs = list(block.get("pairs") or ())
    if len(pairs) != 1:
        raise CharacterizationError("R2 requires exactly one two-source pair")
    pair = pairs[0]
    analysis = analyze_build_pair(pair["old_build"], pair["fresh_build"], pair["delta"])
    delta = pair["delta"]
    before = block["transitions"][0]["before"]
    changed = len(delta.changes)
    total = len(before.nodes) + len(before.edges) + len(before.episodes)
    return {
        "schema_version": "membind.v7.r2-causal-trace.v2",
        "status": "OBSERVER_ONLY",
        "real_graphiti_evidence": block.get("real_graphiti_evidence") is True,
        "source_count": block.get("source_count"),
        "mutation_locality": None if total == 0 else changed / total,
        "delta_change_count": changed,
        "delta": delta,
        "pair_analysis": analysis,
        "completion_order_counted": False,
        "missing_side_is_coverage_only": True,
        "treatment_calls": 0,
        "publication_calls": block.get("native_publication_calls"),
    }


__all__ = [
    "CharacterizationError",
    "analyze_build_pair",
    "audit_r1_assumptions",
    "build_r2_causal_trace",
    "characterize_r3_blocks",
]
