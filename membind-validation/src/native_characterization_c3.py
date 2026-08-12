"""Offline source audit and retained-trace analyzer for frozen C3/E2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from native_characterization_c2_verify import verify_c2_run


DEPENDENCY_MAP_SCHEMA = "membind.native-characterization-dependency-map.v1"
E2_SCHEMA = "membind.native-characterization-e2-opportunity.v1"
TRACE_SCHEMA = "membind.native_characterization.trace.v1"
PHASE_MAP_PATH = "artifacts/native_characterization/phase_map.json"
E1_PATH = "artifacts/native_characterization/e1_breakdown.json"
DEPENDENCY_MAP_PATH = "artifacts/native_characterization/dependency_map.json"
E2_PATH = "artifacts/native_characterization/e2_dependency_opportunity.json"
WORKPLAN_PATH = "../MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
EXPECTED_PHASE_MAP_SHA256 = (
    "afdfd18d17e285fe5b23d9ba8eed2cb893ddabb71723259947a3e7317bd72f31"
)
EXPECTED_WORKPLAN_SHA256 = (
    "be3112cc2da4080ce98f9c94f1ab510ba5cc8350dca108a15e304da04c996b5b"
)

EXPECTED_SOURCE_SHA256 = {
    ".venv/lib/python3.12/site-packages/graphiti_core/graphiti.py": (
        "7c65051a62982d8b510ebdbf37bae4d07020e74520e1f6d9bf8a0ffb26beeccb"
    ),
    ".venv/lib/python3.12/site-packages/graphiti_core/utils/maintenance/"
    "node_operations.py": (
        "14fc92a462bf7f1dd9b70d10a88e27e36a0ddc1594dc18381888209de7137fb4"
    ),
    ".venv/lib/python3.12/site-packages/graphiti_core/utils/maintenance/"
    "edge_operations.py": (
        "b773ff4489968af2a996d5074e679cab9806cc0904a7ff9f2aecc74382325abe"
    ),
    ".venv/lib/python3.12/site-packages/graphiti_core/utils/maintenance/"
    "graph_data_operations.py": (
        "ab5d375738fdd5e8a3aa39242d8dc9b7b281dd0bedb05cd8a7659548582106cb"
    ),
    ".venv/lib/python3.12/site-packages/graphiti_core/utils/bulk_utils.py": (
        "6c7314f24801f0936454b3344788528500432ac5f12692eb36b7d3ef5269f601"
    ),
    ".venv/lib/python3.12/site-packages/graphiti_core/search/search_utils.py": (
        "b55b39f1ec547d40e3c88042830020d8bc2c664bb0601f6996ec856cdcd40808"
    ),
    "src/graphiti_native.py": (
        "f25141000494a8899a40b87f2bf5fb5e5cb519ab2d480d72973aeaf9e0d9c8cc"
    ),
}

_GRAPHITI = ".venv/lib/python3.12/site-packages/graphiti_core/graphiti.py"
_NODE_OPS = (
    ".venv/lib/python3.12/site-packages/graphiti_core/utils/maintenance/"
    "node_operations.py"
)
_EDGE_OPS = (
    ".venv/lib/python3.12/site-packages/graphiti_core/utils/maintenance/"
    "edge_operations.py"
)
_GRAPH_DATA_OPS = (
    ".venv/lib/python3.12/site-packages/graphiti_core/utils/maintenance/"
    "graph_data_operations.py"
)
_BULK_UTILS = ".venv/lib/python3.12/site-packages/graphiti_core/utils/bulk_utils.py"


def _location(path: str, symbol: str, start: int, end: int) -> dict[str, Any]:
    return {
        "path": path,
        "symbol": symbol,
        "line_start": start,
        "line_end": end,
    }


PHASE_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "phase.add_episode",
        "phase": "add-episode",
        "owner": "graphiti_instance",
        "attribute": "add_episode",
        "parent_phase": None,
        "accounting_role": "denominator_root",
        "dependency_class": "unknown",
        "confidence": "high_mixed_container",
        "input_ready_at_arrival": None,
        "transitively_source_derivable": False,
        "timing_eligible": False,
        "potentially_independent_unknown": False,
        "required_upstream_values": [],
        "history_read_set": ["mixed_child_operations"],
        "latest_graph_read_set": ["mixed_child_operations"],
        "mutation_or_publication": "mixed_child_operations",
        "source_locations": [_location(_GRAPHITI, "Graphiti.add_episode", 1070, 1223)],
        "rationale": (
            "Composite root containing D1, D2, and D3 children; its inclusive "
            "duration is the denominator and never an opportunity numerator."
        ),
    },
    {
        "rule_id": "phase.previous_context",
        "phase": "previous-context",
        "owner": "graphiti_instance",
        "attribute": "retrieve_episodes",
        "parent_phase": "add-episode",
        "accounting_role": "classified_interval",
        "dependency_class": "D1",
        "confidence": "high_static_and_dynamic",
        "input_ready_at_arrival": True,
        "transitively_source_derivable": True,
        "timing_eligible": True,
        "potentially_independent_unknown": False,
        "required_upstream_values": ["group_id", "source", "reference_time"],
        "history_read_set": ["ordered_committed_episodic_source_prefix_last_10"],
        "latest_graph_read_set": [],
        "mutation_or_publication": "none",
        "source_locations": [
            _location(_GRAPHITI, "Graphiti.add_episode", 1086, 1095),
            _location(_GRAPH_DATA_OPS, "retrieve_episodes", 130, 167),
        ],
        "rationale": (
            "Reads the immutable ordered episodic source prefix selected by "
            "source and reference time; the same prefix is reconstructable from the log."
        ),
    },
    {
        "rule_id": "phase.node_extraction",
        "phase": "node-extraction",
        "owner": "graphiti_core.graphiti_alias",
        "attribute": "extract_nodes",
        "parent_phase": "add-episode",
        "accounting_role": "classified_interval",
        "dependency_class": "D1",
        "confidence": "high_static_and_dynamic",
        "input_ready_at_arrival": True,
        "transitively_source_derivable": True,
        "timing_eligible": True,
        "potentially_independent_unknown": False,
        "required_upstream_values": ["arriving_episode", "previous_episode_prefix"],
        "history_read_set": ["episode_content_and_previous_episode_content_timestamps"],
        "latest_graph_read_set": [],
        "mutation_or_publication": "none",
        "source_locations": [_location(_NODE_OPS, "extract_nodes", 114, 145)],
        "rationale": (
            "Prompt input is the arriving episode plus immutable previous-episode "
            "content and timestamps, not the current entity or edge graph."
        ),
    },
    {
        "rule_id": "phase.node_resolution",
        "phase": "node-resolution",
        "owner": "graphiti_core.graphiti_alias",
        "attribute": "resolve_extracted_nodes",
        "parent_phase": "add-episode",
        "accounting_role": "classified_interval",
        "dependency_class": "D2",
        "confidence": "high_static_and_dynamic",
        "input_ready_at_arrival": False,
        "transitively_source_derivable": False,
        "timing_eligible": True,
        "potentially_independent_unknown": False,
        "required_upstream_values": ["extracted_nodes"],
        "history_read_set": ["previous_episode_prefix_for_llm_dedup_context"],
        "latest_graph_read_set": ["entity_similarity_candidates"],
        "mutation_or_publication": "resolution_decision_only",
        "source_locations": [
            _location(_NODE_OPS, "_semantic_candidate_search", 407, 449),
            _location(_NODE_OPS, "resolve_extracted_nodes", 627, 705),
        ],
        "rationale": (
            "Queries current Entity candidates and branches on their similarity and "
            "LLM deduplication outcomes."
        ),
    },
    {
        "rule_id": "phase.edge_extraction",
        "phase": "edge-extraction",
        "owner": "graphiti_core.graphiti_alias",
        "attribute": "extract_edges",
        "parent_phase": "add-episode",
        "accounting_role": "classified_interval",
        "dependency_class": "D1",
        "confidence": "high_static_and_dynamic",
        "input_ready_at_arrival": False,
        "transitively_source_derivable": True,
        "timing_eligible": True,
        "potentially_independent_unknown": False,
        "required_upstream_values": ["extracted_nodes", "previous_episode_prefix"],
        "history_read_set": ["arriving_episode_and_previous_episode_prefix"],
        "latest_graph_read_set": [],
        "mutation_or_publication": "none",
        "source_locations": [
            _location(_GRAPHITI, "Graphiti.add_episode", 1139, 1154),
            _location(_EDGE_OPS, "extract_edges", 183, 220),
        ],
        "rationale": (
            "Consumes pre-resolution extracted nodes and source history rather than "
            "resolved graph UUIDs, but extracted nodes are not ready at arrival."
        ),
    },
    {
        "rule_id": "phase.edge_resolution",
        "phase": "edge-resolution",
        "owner": "graphiti_core.graphiti_alias",
        "attribute": "resolve_extracted_edges",
        "parent_phase": "add-episode",
        "accounting_role": "classified_interval",
        "dependency_class": "D2",
        "confidence": "high_static_and_dynamic",
        "input_ready_at_arrival": False,
        "transitively_source_derivable": False,
        "timing_eligible": True,
        "potentially_independent_unknown": False,
        "required_upstream_values": ["uuid_map", "extracted_edges"],
        "history_read_set": ["arriving_episode_for_timestamp_context"],
        "latest_graph_read_set": [
            "between_node_edges",
            "edge_dedup_candidates",
            "edge_invalidation_candidates",
        ],
        "mutation_or_publication": "in_memory_invalidation_and_write_set_decisions",
        "source_locations": [
            _location(_EDGE_OPS, "resolve_extracted_edges", 360, 535),
            _location(_EDGE_OPS, "resolve_extracted_edge", 623, 847),
        ],
        "rationale": (
            "Binds resolved node pointers, queries current edge candidates, and "
            "branches on deduplication and invalidation state."
        ),
    },
    {
        "rule_id": "phase.attributes_summary",
        "phase": "attributes-summary",
        "owner": "graphiti_core.graphiti_alias",
        "attribute": "extract_attributes_from_nodes",
        "parent_phase": "add-episode",
        "accounting_role": "classified_interval",
        "dependency_class": "D2",
        "confidence": "high_static_transitive",
        "input_ready_at_arrival": False,
        "transitively_source_derivable": False,
        "timing_eligible": True,
        "potentially_independent_unknown": False,
        "required_upstream_values": ["resolved_nodes", "new_edges"],
        "history_read_set": ["episode_and_previous_episode_context"],
        "latest_graph_read_set": ["resolved_node_state_and_resolution_derived_new_edges"],
        "mutation_or_publication": "hydrates_resolved_nodes_before_publication",
        "source_locations": [
            _location(_GRAPHITI, "Graphiti.add_episode", 1158, 1167),
            _location(_NODE_OPS, "extract_attributes_from_nodes", 726, 910),
        ],
        "rationale": (
            "Consumes canonical nodes and new-edge outcomes produced by D2 resolution; "
            "absence of a direct DB child does not erase that dependency."
        ),
    },
    {
        "rule_id": "phase.publication",
        "phase": "publication",
        "owner": "graphiti_instance",
        "attribute": "_process_episode_data",
        "parent_phase": "add-episode",
        "accounting_role": "classified_interval",
        "dependency_class": "D3",
        "confidence": "high_static_and_dynamic",
        "input_ready_at_arrival": False,
        "transitively_source_derivable": False,
        "timing_eligible": True,
        "potentially_independent_unknown": False,
        "required_upstream_values": ["hydrated_nodes", "resolved_and_invalidated_edges"],
        "history_read_set": [],
        "latest_graph_read_set": [],
        "mutation_or_publication": "transactional_graph_write_and_visible_frontier",
        "source_locations": [
            _location(_GRAPHITI, "Graphiti.add_episode", 1169, 1179),
            _location(_BULK_UTILS, "add_nodes_and_edges_bulk", 128, 260),
        ],
        "rationale": (
            "Publishes episode, entity, edge, and mention state in the observed write "
            "transaction and defines memory visibility."
        ),
    },
)


OPERATION_REFINEMENTS: tuple[dict[str, Any], ...] = (
    {
        "operation_id": "episode-local-setup",
        "parent_phase": "add-episode",
        "dependency_class": "D0",
        "input_ready_at_arrival": True,
        "transitively_source_derivable": True,
        "timing_eligible": False,
        "source_locations": [_location(_GRAPHITI, "Graphiti.add_episode", 1070, 1119)],
        "rationale": "Validation and local episode/default-map construction lack a distinct span.",
    },
    {
        "operation_id": "node-candidate-embedding",
        "parent_phase": "node-resolution",
        "dependency_class": "D1",
        "input_ready_at_arrival": False,
        "transitively_source_derivable": True,
        "timing_eligible": False,
        "observed_child_selector": {"phase": "candidate-embedding", "operation_class": "node-dedup"},
        "source_locations": [_location(_NODE_OPS, "_semantic_candidate_search", 426, 449)],
        "rationale": "Embedding input is extracted node text, but the child is not a primary E2 interval.",
    },
    {
        "operation_id": "node-candidate-search-and-decision",
        "parent_phase": "node-resolution",
        "dependency_class": "D2",
        "input_ready_at_arrival": False,
        "transitively_source_derivable": False,
        "timing_eligible": False,
        "observed_child_selector": {"phase": "candidate-search", "operation_class": "node-dedup"},
        "source_locations": [_location(_NODE_OPS, "resolve_extracted_nodes", 627, 705)],
        "rationale": "Current graph candidates determine deterministic and LLM resolution.",
    },
    {
        "operation_id": "edge-fact-embedding",
        "parent_phase": "edge-resolution",
        "dependency_class": "D1",
        "input_ready_at_arrival": False,
        "transitively_source_derivable": True,
        "timing_eligible": False,
        "observed_child_selector": {"phase": "candidate-embedding", "operation_class": "edge-presearch"},
        "source_locations": [_location(_EDGE_OPS, "resolve_extracted_edges", 360, 364)],
        "rationale": "Embedding text is the extracted fact, but is nested in the D2 resolution path.",
    },
    {
        "operation_id": "edge-candidate-search",
        "parent_phase": "edge-resolution",
        "dependency_class": "D2",
        "input_ready_at_arrival": False,
        "transitively_source_derivable": False,
        "timing_eligible": False,
        "observed_child_selector": {"phase": "candidate-search", "operation_class": "edge-invalidation"},
        "source_locations": [_location(_EDGE_OPS, "resolve_extracted_edges", 365, 430)],
        "rationale": "Reads current between-node, deduplication, and invalidation candidates.",
    },
    {
        "operation_id": "edge-pointer-and-invalidation-control",
        "parent_phase": "edge-resolution",
        "dependency_class": "D2",
        "input_ready_at_arrival": False,
        "transitively_source_derivable": False,
        "timing_eligible": False,
        "source_locations": [_location(_EDGE_OPS, "resolve_extracted_edge", 684, 847)],
        "rationale": "Resolved pointers and candidate-dependent control determine timestamps and write sets.",
    },
    {
        "operation_id": "community-update",
        "parent_phase": "add-episode",
        "dependency_class": "unknown",
        "input_ready_at_arrival": False,
        "transitively_source_derivable": False,
        "timing_eligible": False,
        "observed": False,
        "source_locations": [_location(_GRAPHITI, "Graphiti.add_episode", 1181, 1191)],
        "rationale": "Excluded because the frozen C2 call did not enable community updates.",
    },
)

_CHILD_PHASES = tuple(rule["phase"] for rule in PHASE_RULES if rule["timing_eligible"])
_RULE_BY_PHASE = {rule["phase"]: rule for rule in PHASE_RULES}


class NativeCharacterizationC3Error(RuntimeError):
    """Sanitized fail-closed offline C3 error."""


def _fail(code: str) -> None:
    raise NativeCharacterizationC3Error(code)


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail("payload_not_canonical_json")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def payload_sha256(value: Mapping[str, Any]) -> str:
    candidate = deepcopy(dict(value))
    candidate.pop("payload_sha256", None)
    return _sha(_canonical_bytes(candidate))


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result.pop("payload_sha256", None)
    result["payload_sha256"] = payload_sha256(result)
    return result


def _validate_seal(value: Mapping[str, Any], code: str) -> None:
    observed = value.get("payload_sha256")
    if not isinstance(observed, str) or observed != payload_sha256(value):
        _fail(code)


def _safe_path(root: Path, relative: str, code: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        _fail(code)
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or relative != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        _fail(code)
    candidate = root
    for part in pure.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            _fail(code)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        _fail(code)
    if not resolved.is_file():
        _fail(code)
    return resolved


def _read_object(path: Path, code: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, dict):
        _fail(code)
    return raw, value


def _source_files(validation: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for relative, expected in sorted(EXPECTED_SOURCE_SHA256.items()):
        path = _safe_path(validation, relative, "source_path_invalid")
        observed = _sha(path.read_bytes())
        if observed != expected:
            _fail("audited_source_hash_mismatch")
        result.append({"path": relative, "sha256": observed})
    return result


def _enrich_locations(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        item = deepcopy(dict(row))
        locations = []
        for location in item.get("source_locations", []):
            bound = deepcopy(dict(location))
            bound["file_sha256"] = EXPECTED_SOURCE_SHA256[bound["path"]]
            locations.append(bound)
        item["source_locations"] = locations
        result.append(item)
    return result


def interval_union_ns(
    intervals: Iterable[tuple[int, int]],
    *,
    clip: tuple[int, int] | None = None,
) -> int:
    """Return overlap-safe interval union length, optionally clipped."""

    normalized: list[tuple[int, int]] = []
    clip_start: int | None = None
    clip_end: int | None = None
    if clip is not None:
        clip_start, clip_end = clip
        if clip_end < clip_start:
            _fail("interval_clip_invalid")
    for start, end in intervals:
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or end < start
        ):
            _fail("interval_invalid")
        if clip is not None:
            start = max(start, int(clip_start))
            end = min(end, int(clip_end))
        if end > start:
            normalized.append((start, end))
    normalized.sort()
    total = 0
    current_start: int | None = None
    current_end: int | None = None
    for start, end in normalized:
        if current_start is None:
            current_start, current_end = start, end
        elif start <= int(current_end):
            current_end = max(int(current_end), end)
        else:
            total += int(current_end) - int(current_start)
            current_start, current_end = start, end
    if current_start is not None:
        total += int(current_end) - int(current_start)
    return total


def _speedup(p: float, concurrency: int) -> float:
    return 1.0 / ((1.0 - p) + p / concurrency)


def _speedup_bounds(p_lower: float, p_upper: float) -> dict[str, Any]:
    return {
        str(concurrency): {
            "at_p_L": _speedup(p_lower, concurrency),
            "at_p_U": _speedup(p_upper, concurrency),
        }
        for concurrency in (2, 4, 8)
    }


def opportunity_bounds(
    *,
    total_ns: int,
    lower_intervals: Iterable[tuple[int, int]],
    possible_unknown_intervals: Iterable[tuple[int, int]],
) -> dict[str, Any]:
    if not isinstance(total_ns, int) or isinstance(total_ns, bool) or total_ns <= 0:
        _fail("total_interval_invalid")
    lower = list(lower_intervals)
    possible = list(possible_unknown_intervals)
    lower_union = interval_union_ns(lower)
    upper_union = interval_union_ns([*lower, *possible])
    if not 0 <= lower_union <= upper_union <= total_ns:
        _fail("opportunity_bounds_invalid")
    p_lower = lower_union / total_ns
    p_upper = upper_union / total_ns
    return {
        "lower_union_ns": lower_union,
        "upper_union_ns": upper_union,
        "p_L": p_lower,
        "p_U": p_upper,
        "speedup_bounds": _speedup_bounds(p_lower, p_upper),
    }


def _span_interval(span: Mapping[str, Any]) -> tuple[int, int]:
    start = span.get("start_ns")
    end = span.get("end_ns")
    duration = span.get("duration_ns")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or not isinstance(duration, int)
        or isinstance(duration, bool)
        or end <= start
        or duration != end - start
    ):
        _fail("span_interval_invalid")
    return start, end


def analyze_interval_set(
    root_span: Mapping[str, Any],
    phase_spans: Sequence[Mapping[str, Any]],
    *,
    rules: Sequence[Mapping[str, Any]] = PHASE_RULES,
) -> dict[str, Any]:
    """Analyze one episode using only the frozen primary phase boundaries."""

    if root_span.get("phase") != "add-episode":
        _fail("add_episode_root_missing")
    root_start, root_end = _span_interval(root_span)
    total = root_end - root_start
    by_phase: dict[str, Mapping[str, Any]] = {}
    for span in phase_spans:
        phase = span.get("phase")
        if not isinstance(phase, str) or phase in by_phase:
            _fail("primary_phase_duplicate_or_invalid")
        by_phase[phase] = span
    if set(by_phase) != set(_CHILD_PHASES):
        _fail("primary_phase_set_mismatch")
    rule_by_phase = {str(rule["phase"]): rule for rule in rules}
    if set(_CHILD_PHASES) - set(rule_by_phase):
        _fail("dependency_rule_missing")

    class_intervals: dict[str, list[tuple[int, int]]] = {
        key: [] for key in ("D0", "D1", "D2", "D3", "unknown")
    }
    lower: list[tuple[int, int]] = []
    non_ready: list[tuple[int, int]] = []
    possible_unknown: list[tuple[int, int]] = []
    all_classified: list[tuple[int, int]] = []
    phase_union: dict[str, int] = {}
    for phase in _CHILD_PHASES:
        span = by_phase[phase]
        interval = _span_interval(span)
        if interval[0] < root_start or interval[1] > root_end:
            _fail("primary_phase_outside_root")
        rule = rule_by_phase[phase]
        dependency_class = rule.get("dependency_class")
        if dependency_class not in class_intervals:
            _fail("dependency_class_invalid")
        class_intervals[str(dependency_class)].append(interval)
        all_classified.append(interval)
        phase_union[phase] = interval_union_ns([interval], clip=(root_start, root_end))
        if dependency_class in {"D0", "D1"}:
            if rule.get("input_ready_at_arrival") is True:
                lower.append(interval)
            else:
                non_ready.append(interval)
        if (
            dependency_class == "unknown"
            and rule.get("potentially_independent_unknown") is True
        ):
            possible_unknown.append(interval)

    covered = interval_union_ns(all_classified, clip=(root_start, root_end))
    root_uncovered = total - covered
    if root_uncovered < 0:
        _fail("root_coverage_invalid")
    class_union = {
        key: interval_union_ns(value, clip=(root_start, root_end))
        for key, value in class_intervals.items()
    }
    class_union["unknown"] += root_uncovered
    bounds = opportunity_bounds(
        total_ns=total,
        lower_intervals=lower,
        possible_unknown_intervals=possible_unknown,
    )
    non_ready_union = interval_union_ns(non_ready, clip=(root_start, root_end))
    return {
        "total_ns": total,
        "class_union_ns": class_union,
        "phase_union_ns": phase_union,
        "root_uncovered_ns": root_uncovered,
        "verified_d0_d1_arrival_ready_union_ns": bounds["lower_union_ns"],
        "verified_d0_d1_non_arrival_ready_union_ns": non_ready_union,
        "potentially_independent_unknown_union_ns": (
            bounds["upper_union_ns"] - bounds["lower_union_ns"]
        ),
        "upper_opportunity_union_ns": bounds["upper_union_ns"],
        "p_L": bounds["p_L"],
        "p_U": bounds["p_U"],
        "speedup_bounds": bounds["speedup_bounds"],
    }


def aggregate_episode_summaries(
    summaries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not summaries:
        _fail("episode_summaries_empty")
    total = sum(int(item["total_ns"]) for item in summaries)
    lower = sum(
        int(item["verified_d0_d1_arrival_ready_union_ns"])
        for item in summaries
    )
    non_ready = sum(
        int(item["verified_d0_d1_non_arrival_ready_union_ns"])
        for item in summaries
    )
    possible_unknown = sum(
        int(item["potentially_independent_unknown_union_ns"])
        for item in summaries
    )
    upper = sum(
        int(item.get("upper_opportunity_union_ns", item["verified_d0_d1_arrival_ready_union_ns"] + item["potentially_independent_unknown_union_ns"]))
        for item in summaries
    )
    root_uncovered = sum(int(item["root_uncovered_ns"]) for item in summaries)
    class_union = {
        key: sum(int(item["class_union_ns"].get(key, 0)) for item in summaries)
        for key in ("D0", "D1", "D2", "D3", "unknown")
    }
    phase_names = sorted(
        {phase for item in summaries for phase in item.get("phase_union_ns", {})}
    )
    phase_union = {
        phase: sum(int(item.get("phase_union_ns", {}).get(phase, 0)) for item in summaries)
        for phase in phase_names
    }
    if total <= 0 or not 0 <= lower <= upper <= total:
        _fail("aggregate_opportunity_invalid")
    p_lower = lower / total
    p_upper = upper / total
    return {
        "episode_count": len(summaries),
        "T_total_ns": total,
        "class_union_ns": class_union,
        "class_union_fraction": {key: value / total for key, value in class_union.items()},
        "phase_union_ns": phase_union,
        "phase_union_fraction": {key: value / total for key, value in phase_union.items()},
        "root_uncovered_ns": root_uncovered,
        "verified_d0_d1_arrival_ready_union_ns": lower,
        "verified_d0_d1_non_arrival_ready_union_ns": non_ready,
        "potentially_independent_unknown_union_ns": possible_unknown,
        "upper_opportunity_union_ns": upper,
        "p_L": p_lower,
        "p_U": p_upper,
        "speedup_bounds": _speedup_bounds(p_lower, p_upper),
    }


def _load_verified_run(
    validation: Path, run_id: str
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    verification = verify_c2_run(validation, run_id)
    if verification.get("status") != "verified":
        _fail("c2_not_verified")
    run_root = validation / "artifacts/native_characterization/runs" / run_id
    manifest_raw, manifest = _read_object(run_root / "manifest.json", "manifest_invalid")
    if verification.get("manifest_sha256") != _sha(manifest_raw):
        _fail("manifest_verification_mismatch")
    trace_paths = sorted(
        relative
        for relative in manifest.get("artifact_sha256", {})
        if relative.startswith("blocks/") and relative.endswith("/trace.jsonl")
    )
    envelopes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for relative in trace_paths:
        path = _safe_path(run_root, relative, "trace_path_invalid")
        raw = path.read_bytes()
        if _sha(raw) != manifest["artifact_sha256"][relative]:
            _fail("trace_hash_mismatch")
        directory = PurePosixPath(relative).parts[1]
        block_index = int(directory.split("_", 1)[0])
        for line in raw.splitlines():
            try:
                envelope = json.loads(line.decode("ascii"))
            except (UnicodeError, json.JSONDecodeError):
                _fail("trace_json_invalid")
            if not isinstance(envelope, dict):
                _fail("trace_json_invalid")
            _validate_seal(envelope, "trace_payload_invalid")
            episode_id = envelope.get("episode_id")
            if (
                envelope.get("schema_version") != TRACE_SCHEMA
                or envelope.get("run_id") != run_id
                or not isinstance(episode_id, str)
                or episode_id in seen
            ):
                _fail("trace_contract_invalid")
            seen.add(episode_id)
            item = deepcopy(envelope)
            item["block_index"] = block_index
            item["history_id"] = episode_id.split(":", 1)[0]
            envelopes.append(item)
    envelopes.sort(key=lambda item: (item["block_index"], item["source_sequence"]))
    if len(envelopes) != manifest.get("episode_count"):
        _fail("trace_episode_count_mismatch")
    return verification, manifest, envelopes


def _nearest_primary_phase(
    span: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]
) -> str | None:
    parent_id = span.get("parent_span_id")
    visited: set[str] = set()
    while isinstance(parent_id, str):
        if parent_id in visited:
            _fail("span_parent_cycle")
        visited.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            _fail("span_parent_missing")
        phase = parent.get("phase")
        if phase in _CHILD_PHASES:
            return str(phase)
        parent_id = parent.get("parent_span_id")
    return None


def _dynamic_corroboration(envelopes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    db_queries: Counter[str] = Counter()
    db_writes: Counter[str] = Counter()
    transactions: Counter[str] = Counter()
    prompts: Counter[str] = Counter()
    prefix_nonzero = 0
    node_candidates = 0
    node_calls = 0
    node_nonzero = 0
    edge_candidates = 0
    edge_calls = 0
    edge_nonzero = 0
    for envelope in envelopes:
        spans = envelope.get("spans")
        if not isinstance(spans, list):
            _fail("trace_spans_invalid")
        by_id = {
            str(span["span_id"]): span
            for span in spans
            if isinstance(span, Mapping) and isinstance(span.get("span_id"), str)
        }
        for span in spans:
            if not isinstance(span, Mapping):
                _fail("trace_spans_invalid")
            phase = span.get("phase")
            operation = span.get("operation_class")
            metadata = span.get("metadata") if isinstance(span.get("metadata"), Mapping) else {}
            owner = _nearest_primary_phase(span, by_id)
            if phase == "graph-prefix-snapshot":
                if int(metadata.get("graph_prefix_node_count", 0)) > 0 or int(
                    metadata.get("graph_prefix_relationship_count", 0)
                ) > 0:
                    prefix_nonzero += 1
            elif phase == "database" and owner is not None:
                if operation == "write":
                    db_writes[owner] += 1
                else:
                    db_queries[owner] += 1
            elif phase == "database-transaction" and owner is not None:
                transactions[owner] += 1
            elif phase == "llm" and operation == "logical-call":
                prompt = metadata.get("prompt_name")
                if isinstance(prompt, str):
                    prompts[prompt] += 1
            if phase == "candidate-search" and operation == "node-dedup":
                count = int(metadata.get("candidate_count", 0))
                node_candidates += count
                node_calls += 1
                node_nonzero += int(count > 0)
            if phase == "candidate-search" and operation == "edge-invalidation":
                count = int(metadata.get("candidate_count", 0))
                edge_candidates += count
                edge_calls += 1
                edge_nonzero += int(count > 0)
    return {
        "episode_count": len(envelopes),
        "nonzero_graph_prefix_episode_count": prefix_nonzero,
        "db_query_count_by_phase": dict(sorted(db_queries.items())),
        "db_write_count_by_phase": dict(sorted(db_writes.items())),
        "db_transaction_count_by_phase": dict(sorted(transactions.items())),
        "prompt_call_count": dict(sorted(prompts.items())),
        "node_dedup_candidate_count": node_candidates,
        "node_dedup_call_count": node_calls,
        "node_dedup_nonzero_call_count": node_nonzero,
        "edge_invalidation_candidate_count": edge_candidates,
        "edge_invalidation_call_count": edge_calls,
        "edge_invalidation_nonzero_call_count": edge_nonzero,
        "captured_read_identity_scope": "counts_only_no_entity_or_edge_candidate_ids",
    }


def _phase_map(validation: Path, manifest: Mapping[str, Any]) -> tuple[bytes, dict[str, Any]]:
    path = _safe_path(validation, PHASE_MAP_PATH, "phase_map_path_invalid")
    raw, value = _read_object(path, "phase_map_invalid")
    _validate_seal(value, "phase_map_payload_invalid")
    if (
        _sha(raw) != EXPECTED_PHASE_MAP_SHA256
        or manifest.get("provenance", {}).get("phase_map_sha256") != _sha(raw)
    ):
        _fail("phase_map_hash_mismatch")
    observed = [
        (item.get("phase"), item.get("owner"), item.get("attribute"))
        for item in value.get("phases", [])
        if isinstance(item, Mapping)
    ]
    expected = [
        (rule["phase"], rule["owner"], rule["attribute"])
        for rule in PHASE_RULES
    ]
    if observed != expected:
        _fail("phase_map_surface_mismatch")
    return raw, value


def _workplan(validation: Path) -> tuple[str, str]:
    path = (validation / WORKPLAN_PATH).resolve()
    try:
        path.relative_to(validation.parent)
        raw = path.read_bytes()
    except (OSError, ValueError):
        _fail("workplan_path_invalid")
    digest = _sha(raw)
    if digest != EXPECTED_WORKPLAN_SHA256:
        _fail("workplan_hash_mismatch")
    return WORKPLAN_PATH, digest


def build_dependency_map(
    validation_root: str | Path, run_id: str
) -> dict[str, Any]:
    """Build the static/dynamic dependency ledger without mutating evidence."""

    validation = Path(validation_root).resolve()
    verification, manifest, envelopes = _load_verified_run(validation, run_id)
    phase_map_raw, phase_map = _phase_map(validation, manifest)
    workplan_path, workplan_sha = _workplan(validation)
    source_files = _source_files(validation)
    dynamic = _dynamic_corroboration(envelopes)
    freeze_path = str(manifest["provenance"]["freeze_path"])
    freeze = _safe_path(validation, freeze_path, "freeze_path_invalid")
    freeze_raw = freeze.read_bytes()
    if _sha(freeze_raw) != manifest.get("freeze_sha256"):
        _fail("freeze_hash_mismatch")
    graphiti_identity = manifest.get("provenance", {}).get("sanitized_runtime_identity", {}).get("graphiti")
    if graphiti_identity != {
        "commit": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        "version": "0.29.3",
    }:
        _fail("graphiti_identity_mismatch")

    phase_rules = _enrich_locations(PHASE_RULES)
    for rule in phase_rules:
        phase = rule["phase"]
        rule["dynamic_counts"] = {
            "db_query_count": dynamic["db_query_count_by_phase"].get(phase, 0),
            "db_write_count": dynamic["db_write_count_by_phase"].get(phase, 0),
            "db_transaction_count": dynamic["db_transaction_count_by_phase"].get(phase, 0),
        }
    result = {
        "schema_version": DEPENDENCY_MAP_SCHEMA,
        "artifact_id": "native-characterization-dependency-map",
        "stage": "C3/E2",
        "status": "complete",
        "run_id": run_id,
        "creation_command": (
            ".venv/bin/python src/native_characterization_c3.py "
            f"--validation-root . --run-id {run_id} --write"
        ),
        "provenance": {
            "c2_verification": verification,
            "manifest_sha256": verification["manifest_sha256"],
            "checkpoint_sha256": verification["checkpoint_sha256"],
            "e1_breakdown_sha256": verification["e1_breakdown_sha256"],
            "freeze_path": freeze_path,
            "freeze_sha256": _sha(freeze_raw),
            "phase_map_path": PHASE_MAP_PATH,
            "phase_map_sha256": _sha(phase_map_raw),
            "phase_map_payload_sha256": phase_map["payload_sha256"],
            "workplan_path": workplan_path,
            "workplan_sha256": workplan_sha,
            "graphiti_identity": graphiti_identity,
            "audited_source_files": source_files,
            "builder_source_sha256": _sha(Path(__file__).read_bytes()),
        },
        "dependency_class_definitions": {
            "D0": "arriving episode and immutable local inputs only",
            "D1": "immutable source/history prefix or values derived from it",
            "D2": "latest materialized graph read or branch dependency",
            "D3": "graph mutation or visible publication frontier",
            "unknown": "incomplete, mixed, or conflicting evidence",
        },
        "phase_rules": phase_rules,
        "operation_refinements": _enrich_locations(OPERATION_REFINEMENTS),
        "dynamic_corroboration": dynamic,
        "limitations": [
            "No counterfactual dependency microexperiment is performed.",
            "Zero observed reads never proves independence.",
            "Candidate and query telemetry contains counts, not entity or edge read identities.",
            "The add-episode exclusive residue remains unknown and is not potentially independent.",
        ],
        "interpretation": "bounded_native_graphiti_dependency_screening_not_mechanism_authorization",
    }
    return _seal(result)


def _descendant_evidence(
    span_id: str,
    spans: Sequence[Mapping[str, Any]],
    by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    descendants: list[Mapping[str, Any]] = []
    for candidate in spans:
        parent = candidate.get("parent_span_id")
        visited: set[str] = set()
        while isinstance(parent, str):
            if parent in visited:
                _fail("span_parent_cycle")
            visited.add(parent)
            if parent == span_id:
                descendants.append(candidate)
                break
            owner = by_id.get(parent)
            if owner is None:
                _fail("span_parent_missing")
            parent = owner.get("parent_span_id")
    prompts: Counter[str] = Counter()
    candidate_count = 0
    candidate_query_count = 0
    db_queries = 0
    db_writes = 0
    transactions = 0
    for child in descendants:
        phase = child.get("phase")
        operation = child.get("operation_class")
        metadata = child.get("metadata") if isinstance(child.get("metadata"), Mapping) else {}
        if phase == "llm" and operation == "logical-call" and isinstance(metadata.get("prompt_name"), str):
            prompts[str(metadata["prompt_name"])] += 1
        if phase == "candidate-search":
            candidate_count += int(metadata.get("candidate_count", 0))
            candidate_query_count += int(metadata.get("candidate_query_count", 0))
        if phase == "database":
            if operation == "write":
                db_writes += 1
            else:
                db_queries += 1
        if phase == "database-transaction":
            transactions += 1
    return {
        "llm_prompt_call_count": dict(sorted(prompts.items())),
        "candidate_count": candidate_count,
        "candidate_query_count": candidate_query_count,
        "db_query_count": db_queries,
        "db_write_count": db_writes,
        "db_transaction_count": transactions,
    }


def _episode_analysis(
    envelope: Mapping[str, Any], rules: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    spans = envelope.get("spans")
    if not isinstance(spans, list):
        _fail("trace_spans_invalid")
    by_id = {
        str(span["span_id"]): span
        for span in spans
        if isinstance(span, Mapping) and isinstance(span.get("span_id"), str)
    }
    roots = [span for span in spans if isinstance(span, Mapping) and span.get("phase") == "add-episode"]
    if len(roots) != 1:
        _fail("add_episode_root_count_mismatch")
    root = roots[0]
    root_id = root.get("span_id")
    primary = [
        span
        for span in spans
        if isinstance(span, Mapping)
        and span.get("phase") in _CHILD_PHASES
        and span.get("parent_span_id") == root_id
    ]
    summary = analyze_interval_set(root, primary, rules=list(rules.values()))
    summary.update(
        {
            "block_index": envelope["block_index"],
            "history_id": envelope["history_id"],
            "episode_id": envelope["episode_id"],
            "source_sequence": envelope["source_sequence"],
        }
    )
    prefix_spans = [span for span in spans if isinstance(span, Mapping) and span.get("phase") == "graph-prefix-snapshot"]
    if len(prefix_spans) != 1:
        _fail("graph_prefix_snapshot_count_mismatch")
    prefix_metadata = prefix_spans[0].get("metadata") if isinstance(prefix_spans[0].get("metadata"), Mapping) else {}
    prefix_size = {
        "node_count": int(prefix_metadata.get("graph_prefix_node_count", 0)),
        "relationship_count": int(prefix_metadata.get("graph_prefix_relationship_count", 0)),
    }
    intervals: list[dict[str, Any]] = []
    span_by_phase = {str(span["phase"]): span for span in primary}
    span_by_phase["add-episode"] = root
    for rule in PHASE_RULES:
        phase = str(rule["phase"])
        span = span_by_phase[phase]
        start, end = _span_interval(span)
        dependency_class = str(rule["dependency_class"])
        intervals.append(
            {
                "block_index": envelope["block_index"],
                "history_id": envelope["history_id"],
                "episode_id": envelope["episode_id"],
                "source_sequence": envelope["source_sequence"],
                "episode_source_sha256": envelope["episode_source_sha256"],
                "prefix_sha256": envelope["prefix_sha256"],
                "phase": phase,
                "span_id": span["span_id"],
                "start_ns": start,
                "end_ns": end,
                "duration_ns": end - start,
                "dependency_rule_id": rule["rule_id"],
                "dependency_class": dependency_class,
                "confidence": rule["confidence"],
                "input_ready_at_arrival": rule["input_ready_at_arrival"],
                "lower_bound_eligible": (
                    dependency_class in {"D0", "D1"}
                    and rule["input_ready_at_arrival"] is True
                    and rule["timing_eligible"] is True
                ),
                "possible_unknown_eligible": (
                    dependency_class == "unknown"
                    and rule["potentially_independent_unknown"] is True
                    and rule["timing_eligible"] is True
                ),
                "accounting_role": rule["accounting_role"],
                "graph_prefix_size": prefix_size,
                "observed_dynamic_evidence": _descendant_evidence(str(span["span_id"]), spans, by_id),
            }
        )
    return summary, intervals


def _validate_dependency_map(
    dependency_map: Mapping[str, Any], run_id: str, manifest: Mapping[str, Any]
) -> None:
    _validate_seal(dependency_map, "dependency_map_payload_invalid")
    expected_rules = _enrich_locations(PHASE_RULES)
    dynamic_by_phase = {
        str(item["phase"]): item.get("dynamic_counts")
        for item in dependency_map.get("phase_rules", [])
        if isinstance(item, Mapping)
    }
    for rule in expected_rules:
        rule["dynamic_counts"] = dynamic_by_phase.get(str(rule["phase"]))
    exact = (
        dependency_map.get("schema_version") == DEPENDENCY_MAP_SCHEMA
        and dependency_map.get("status") == "complete"
        and dependency_map.get("run_id") == run_id
        and dependency_map.get("phase_rules") == expected_rules
        and dependency_map.get("provenance", {}).get("manifest_sha256")
        == _sha(_canonical_file_bytes(manifest))
    )
    if not exact:
        _fail("dependency_map_contract_mismatch")


def _canonical_file_bytes(value: Mapping[str, Any]) -> bytes:
    # Completed runner artifacts are canonical JSON followed by a newline.
    return _canonical_bytes(value) + b"\n"


def analyze_e2(
    validation_root: str | Path,
    run_id: str,
    dependency_map: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify retained C2 intervals and compute frozen E2 opportunity bounds."""

    validation = Path(validation_root).resolve()
    verification, manifest, envelopes = _load_verified_run(validation, run_id)
    _validate_dependency_map(dependency_map, run_id, manifest)
    rule_by_phase = {
        str(rule["phase"]): rule
        for rule in dependency_map["phase_rules"]
    }
    summaries: list[dict[str, Any]] = []
    intervals: list[dict[str, Any]] = []
    for envelope in envelopes:
        summary, episode_intervals = _episode_analysis(envelope, rule_by_phase)
        summaries.append(summary)
        intervals.extend(episode_intervals)

    history_order: list[str] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for summary in summaries:
        history = str(summary["history_id"])
        if history not in grouped:
            history_order.append(history)
        grouped[history].append(summary)
    histories = []
    for history in history_order:
        aggregate = aggregate_episode_summaries(grouped[history])
        aggregate["history_id"] = history
        aggregate["block_index"] = grouped[history][0]["block_index"]
        histories.append(aggregate)
    aggregate = aggregate_episode_summaries(summaries)

    e1_raw, e1 = _read_object(
        _safe_path(validation, E1_PATH, "e1_path_invalid"), "e1_invalid"
    )
    if _sha(e1_raw) != verification["e1_breakdown_sha256"]:
        _fail("e1_hash_mismatch")
    if e1.get("aggregate", {}).get("total_add_episode_union_ns") != aggregate["T_total_ns"]:
        _fail("e1_total_crosscheck_mismatch")
    for phase in _CHILD_PHASES:
        expected = e1.get("aggregate_phase_occupancy", {}).get(phase, {}).get("union_ns")
        if expected != aggregate["phase_union_ns"].get(phase):
            _fail("e1_phase_crosscheck_mismatch")

    result = {
        "schema_version": E2_SCHEMA,
        "artifact_id": "native-characterization-e2-dependency-opportunity",
        "stage": "C3/E2",
        "status": "complete",
        "run_id": run_id,
        "creation_command": (
            ".venv/bin/python src/native_characterization_c3.py "
            f"--validation-root . --run-id {run_id} --write"
        ),
        "provenance": {
            "c2_verification": verification,
            "manifest_sha256": verification["manifest_sha256"],
            "checkpoint_sha256": verification["checkpoint_sha256"],
            "e1_breakdown_path": E1_PATH,
            "e1_breakdown_sha256": _sha(e1_raw),
            "freeze_sha256": manifest["freeze_sha256"],
            "phase_map_sha256": manifest["provenance"]["phase_map_sha256"],
            "dependency_map_payload_sha256": dependency_map["payload_sha256"],
            "analyzer_source_sha256": _sha(Path(__file__).read_bytes()),
            "trace_files": [
                {
                    "path": relative,
                    "sha256": manifest["artifact_sha256"][relative],
                }
                for relative in sorted(manifest["artifact_sha256"])
                if relative.startswith("blocks/") and relative.endswith("/trace.jsonl")
            ],
        },
        "dependency_rules_summary": [
            {
                "rule_id": rule["rule_id"],
                "phase": rule["phase"],
                "dependency_class": rule["dependency_class"],
                "input_ready_at_arrival": rule["input_ready_at_arrival"],
                "timing_eligible": rule["timing_eligible"],
                "potentially_independent_unknown": rule[
                    "potentially_independent_unknown"
                ],
            }
            for rule in dependency_map["phase_rules"]
        ],
        "intervals": intervals,
        "histories": histories,
        "aggregate": aggregate,
        "telemetry_completeness": {
            "status": "complete",
            "episode_count": len(summaries),
            "classified_primary_interval_count": len(intervals),
            "missing_required_fields": [],
        },
        "limitations": [
            "Bounds are structural and ignore remote capacity, batching, contention, and ordering costs.",
            "D1 edge extraction is reported separately because its extracted-node input is not ready at arrival.",
            "Root uncovered time remains unknown and cannot increase p_U.",
            "No hard speedup threshold or mechanism authorization is inferred.",
        ],
        "interpretation": "descriptive_structural_upper_bounds_for_frozen_native_graphiti_trace",
    }
    return _seal(result)


def _atomic_write_equal_or_new(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = _canonical_bytes(payload) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        _fail("output_path_symlink")
    if path.exists():
        if path.read_bytes() != encoded:
            _fail("output_artifact_drift")
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        _fail("output_artifact_write_failed")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_c3_artifacts(
    validation_root: str | Path, run_id: str
) -> dict[str, Any]:
    validation = Path(validation_root).resolve()
    dependency_map = build_dependency_map(validation, run_id)
    e2 = analyze_e2(validation, run_id, dependency_map)
    dependency_path = validation / DEPENDENCY_MAP_PATH
    e2_path = validation / E2_PATH
    _atomic_write_equal_or_new(dependency_path, dependency_map)
    _atomic_write_equal_or_new(e2_path, e2)
    return {
        "status": "complete",
        "run_id": run_id,
        "dependency_map_path": DEPENDENCY_MAP_PATH,
        "dependency_map_sha256": _sha(dependency_path.read_bytes()),
        "dependency_map_payload_sha256": dependency_map["payload_sha256"],
        "e2_path": E2_PATH,
        "e2_sha256": _sha(e2_path.read_bytes()),
        "e2_payload_sha256": e2["payload_sha256"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.write:
            result = write_c3_artifacts(args.validation_root, args.run_id)
        else:
            dependency_map = build_dependency_map(args.validation_root, args.run_id)
            e2 = analyze_e2(args.validation_root, args.run_id, dependency_map)
            result = {
                "status": "dry_run_complete",
                "run_id": args.run_id,
                "dependency_map_payload_sha256": dependency_map["payload_sha256"],
                "e2_payload_sha256": e2["payload_sha256"],
                "aggregate": e2["aggregate"],
            }
    except NativeCharacterizationC3Error as exc:
        print(json.dumps({"status": "error", "error_code": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXPECTED_SOURCE_SHA256",
    "NativeCharacterizationC3Error",
    "PHASE_RULES",
    "aggregate_episode_summaries",
    "analyze_e2",
    "analyze_interval_set",
    "build_dependency_map",
    "interval_union_ns",
    "opportunity_bounds",
    "payload_sha256",
    "write_c3_artifacts",
]
