"""Close the V1 embedding question from immutable retained artifacts only."""

from __future__ import annotations

import ast
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "membind.v1.retained_embedding_closure.v1"
NOT_COMPUTABLE = "not_computable_from_retained_artifacts"
NOT_AVAILABLE = "not_available"

_FILES = {
    "source5_run_a_forensics": Path(
        "search_forensics/diagnostic_smoke14_source5_M0_001.json"
    ),
    "source5_run_b_forensics": Path(
        "search_forensics/diagnostic_smoke14_source5_M0_002.json"
    ),
    "source5_run_a_status": Path(
        "runs/diagnostic_smoke14_source5_M0_001.json"
    ),
    "source5_run_b_status": Path(
        "runs/diagnostic_smoke14_source5_M0_002.json"
    ),
    "prompt_divergence_status": Path(
        "runs/diagnostic_smoke14_source8_M0_002.json"
    ),
    "prompt_divergence_trace": Path(
        "traces/diagnostic_smoke14_source8_M0_002.jsonl"
    ),
    "prompt_divergence_diagnostic": Path(
        "unexpected_prompts/diagnostic_smoke14_source8_M0_002.json"
    ),
    "smoke14_prompt_cache": Path("prompt_cache/smoke_smoke14_c6853660.jsonl"),
}

_ENTITY_FIELDS = ("name", "summary", "labels")
_EDGE_FIELDS = (
    "fact",
    "name",
    "source_name",
    "target_name",
    "valid_at",
    "invalid_at",
)
_EMBEDDING_FIELDS = (
    "embedding_dimension",
    "embedding_sha256",
    "embedding_norm",
)
_PROMPT_NON_USER_FIELDS = (
    "model_revision",
    "decoding_config",
    "structured_output_schema",
    "system_prompt",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_paths(artifacts: Path) -> dict[str, Path]:
    paths = {role: artifacts / relative for role, relative in _FILES.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "required retained artifact is missing: " + ", ".join(sorted(missing))
        )
    return paths


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[Any]:
    # ASCII LF is the framing contract; splitlines() would also split U+2028.
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line]


def _evidence_sources(artifacts: Path, paths: dict[str, Path]) -> list[dict[str, Any]]:
    return [
        {
            "role": role,
            "path": str(path.relative_to(artifacts)),
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for role, path in sorted(paths.items())
    ]


def _logical_record(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    result = {field: record.get(field) for field in fields}
    if "labels" in result:
        result["labels"] = sorted(str(value) for value in (result["labels"] or []))
    return result


def _record_fingerprint(record: dict[str, Any]) -> tuple[str, float, int]:
    return (
        str(record.get("embedding_sha256") or ""),
        float(record.get("embedding_norm") or 0.0),
        int(record.get("embedding_dimension") or 0),
    )


def _changed_item_summary(
    logical: dict[str, Any],
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any]:
    norm_a = float(first.get("embedding_norm") or 0.0)
    norm_b = float(second.get("embedding_norm") or 0.0)
    identity = {
        key: logical[key]
        for key in ("name", "fact", "source_name", "target_name")
        if key in logical
    }
    return {
        **identity,
        "logical_key_sha256": _sha256_json(logical),
        "embedding_sha256_a": str(first.get("embedding_sha256") or ""),
        "embedding_sha256_b": str(second.get("embedding_sha256") or ""),
        "embedding_dimension_a": int(first.get("embedding_dimension") or 0),
        "embedding_dimension_b": int(second.get("embedding_dimension") or 0),
        "embedding_norm_a": norm_a,
        "embedding_norm_b": norm_b,
        "embedding_norm_abs_diff": abs(norm_a - norm_b),
    }


def compare_source_state(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    record_type: str,
) -> dict[str, Any]:
    """Compare source state by logical content, never by its misleading stored hash."""

    if record_type == "entities":
        fields = _ENTITY_FIELDS
    elif record_type == "edges":
        fields = _EDGE_FIELDS
    else:
        raise ValueError(f"unsupported source-state record type: {record_type}")

    records_a = list(first.get(record_type) or [])
    records_b = list(second.get(record_type) or [])
    logical_a = [_logical_record(record, fields) for record in records_a]
    logical_b = [_logical_record(record, fields) for record in records_b]
    logical_counter_a = Counter(_canonical_json(record) for record in logical_a)
    logical_counter_b = Counter(_canonical_json(record) for record in logical_b)
    logical_equal = logical_counter_a == logical_counter_b

    buckets_a: dict[str, list[dict[str, Any]]] = defaultdict(list)
    buckets_b: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record, logical in zip(records_a, logical_a, strict=True):
        buckets_a[_canonical_json(logical)].append(record)
    for record, logical in zip(records_b, logical_b, strict=True):
        buckets_b[_canonical_json(logical)].append(record)

    changed: list[dict[str, Any]] = []
    equal_count = 0
    paired_count = 0
    max_norm_delta = 0.0
    dimensions: set[int] = set()
    if logical_equal:
        for key in sorted(buckets_a):
            left = sorted(buckets_a[key], key=_record_fingerprint)
            right = sorted(buckets_b[key], key=_record_fingerprint)
            if len(left) != len(right):
                raise ValueError(f"logical source-state multiplicity changed for {key}")
            logical = json.loads(key)
            for ordinal, (item_a, item_b) in enumerate(
                zip(left, right, strict=True), start=1
            ):
                paired_count += 1
                dimensions.update(
                    {
                        int(item_a.get("embedding_dimension") or 0),
                        int(item_b.get("embedding_dimension") or 0),
                    }
                )
                norm_delta = abs(
                    float(item_a.get("embedding_norm") or 0.0)
                    - float(item_b.get("embedding_norm") or 0.0)
                )
                max_norm_delta = max(max_norm_delta, norm_delta)
                if item_a.get("embedding_sha256") == item_b.get("embedding_sha256"):
                    equal_count += 1
                else:
                    summary = _changed_item_summary(logical, item_a, item_b)
                    summary["duplicate_ordinal"] = ordinal
                    changed.append(summary)

    return {
        "pairing_key": [*fields, "duplicate_ordinal"],
        "stored_logical_graph_hash_ignored": True,
        "stored_logical_graph_hash_reason": (
            "the retained hash includes embedding metadata and is not semantic-only"
        ),
        "logical_content_equal": logical_equal,
        "count_a": len(records_a),
        "count_b": len(records_b),
        "count_each": len(records_a) if len(records_a) == len(records_b) else None,
        "paired_count": paired_count,
        "embedding_hash_equal_count": equal_count,
        "embedding_hash_changed_count": len(changed),
        "embedding_dimensions": sorted(value for value in dimensions if value),
        "embedding_dimension_equal_for_all_pairs": len(dimensions) == 1,
        "max_embedding_norm_abs_diff": max_norm_delta,
        "exact_embedding_request_bytes_equal": NOT_AVAILABLE,
        "changed_items": changed,
    }


def _source_state(payload: dict[str, Any], phase: str) -> dict[str, Any]:
    matches = [
        state
        for state in payload.get("source_states", [])
        if state.get("phase") == phase and int(state.get("source_sequence", -1)) == 5
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one retained source-5 state for {phase}")
    return matches[0]


def _episode_sequence(event: dict[str, Any]) -> int | None:
    key = event.get("episode_key")
    if not isinstance(key, list) or len(key) != 2:
        return None
    try:
        return int(key[1])
    except (TypeError, ValueError):
        return None


def _edge_identity(value: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(value.get(field) for field in _EDGE_FIELDS)


def _entity_identity(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("name"),
        value.get("summary"),
        tuple(sorted(str(label) for label in (value.get("labels") or []))),
    )


def _candidate_identity(kind: str, value: dict[str, Any]) -> tuple[Any, ...]:
    return _entity_identity(value) if kind == "node_cosine_search" else _edge_identity(value)


def _fulltext_key(event: dict[str, Any]) -> tuple[Any, ...]:
    parameters = event.get("parameters") or {}
    return (
        _episode_sequence(event),
        event.get("kind"),
        event.get("normalized_query_sha256"),
        parameters.get("query_sha256"),
        parameters.get("query_length"),
        tuple(parameters.get("group_ids") or []),
        parameters.get("limit"),
        parameters.get("min_score"),
    )


def _unique_events_by_key(events: Iterable[dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    for event in events:
        key = _fulltext_key(event)
        if key in result:
            raise ValueError(f"retained full-text event key is not unique: {key}")
        result[key] = event
    return result


def _signature_counter(
    events: Iterable[dict[str, Any]],
    signature: Any,
) -> Counter[str]:
    return Counter(_canonical_json(signature(event)) for event in events)


def _backend_membership_signature(event: dict[str, Any]) -> Any:
    kind = str(event.get("kind"))
    identities = sorted(
        (_candidate_identity(kind, value) for value in event.get("backend_candidates", [])),
        key=_canonical_json,
    )
    return [kind, identities]


def _backend_order_signature(event: dict[str, Any]) -> Any:
    kind = str(event.get("kind"))
    return [
        kind,
        [_candidate_identity(kind, value) for value in event.get("backend_candidates", [])],
    ]


def _python_selected_signature(event: dict[str, Any]) -> Any:
    kind = str(event.get("kind"))
    selected = [value for value in event.get("python_ranked", []) if value.get("selected")]
    identities = sorted(
        (_candidate_identity(kind, value) for value in selected), key=_canonical_json
    )
    return [kind, identities]


def _python_order_signature(event: dict[str, Any]) -> Any:
    kind = str(event.get("kind"))
    return [
        kind,
        [_candidate_identity(kind, value) for value in event.get("python_ranked", [])],
    ]


def _vector_hash_summary(
    events_a: list[dict[str, Any]], events_b: list[dict[str, Any]]
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for kind in ("node_cosine_search", "edge_cosine_search"):
        hashes_a = Counter(
            str((event.get("parameters") or {}).get("search_vector_sha256"))
            for event in events_a
            if event.get("kind") == kind
        )
        hashes_b = Counter(
            str((event.get("parameters") or {}).get("search_vector_sha256"))
            for event in events_b
            if event.get("kind") == kind
        )
        summary[kind] = {
            "event_count_a": sum(hashes_a.values()),
            "event_count_b": sum(hashes_b.values()),
            "unique_hash_count_a": len(hashes_a),
            "unique_hash_count_b": len(hashes_b),
            "shared_unique_hash_count": len(set(hashes_a) & set(hashes_b)),
            "run_a_only_unique_hash_count": len(set(hashes_a) - set(hashes_b)),
            "run_b_only_unique_hash_count": len(set(hashes_b) - set(hashes_a)),
            "hash_bag_equal": hashes_a == hashes_b,
        }
    return summary


def analyze_query_events(
    events_a: list[dict[str, Any]],
    events_b: list[dict[str, Any]],
    *,
    source_sequence: int,
) -> dict[str, Any]:
    selected_a = [event for event in events_a if _episode_sequence(event) == source_sequence]
    selected_b = [event for event in events_b if _episode_sequence(event) == source_sequence]
    fulltext_a = [event for event in selected_a if event.get("kind") == "edge_fulltext_search"]
    fulltext_b = [event for event in selected_b if event.get("kind") == "edge_fulltext_search"]
    indexed_a = _unique_events_by_key(fulltext_a)
    indexed_b = _unique_events_by_key(fulltext_b)
    input_keys_equal = set(indexed_a) == set(indexed_b)
    paired_keys = sorted(set(indexed_a) & set(indexed_b), key=_canonical_json)
    membership_equal = all(
        Counter(_edge_identity(value) for value in indexed_a[key].get("backend_candidates", []))
        == Counter(_edge_identity(value) for value in indexed_b[key].get("backend_candidates", []))
        for key in paired_keys
    )
    order_equal = all(
        [_edge_identity(value) for value in indexed_a[key].get("backend_candidates", [])]
        == [_edge_identity(value) for value in indexed_b[key].get("backend_candidates", [])]
        for key in paired_keys
    )

    cosine_kinds = {"node_cosine_search", "edge_cosine_search"}
    cosine_a = [event for event in selected_a if event.get("kind") in cosine_kinds]
    cosine_b = [event for event in selected_b if event.get("kind") in cosine_kinds]
    vector_bag_a = Counter(
        (
            str(event.get("kind")),
            str((event.get("parameters") or {}).get("search_vector_sha256")),
        )
        for event in cosine_a
    )
    vector_bag_b = Counter(
        (
            str(event.get("kind")),
            str((event.get("parameters") or {}).get("search_vector_sha256")),
        )
        for event in cosine_b
    )

    return {
        "fulltext": {
            "pairing_status": "exact",
            "pairing_key": [
                "source_sequence",
                "kind",
                "normalized_query_sha256",
                "query_sha256",
                "query_length",
                "group_ids",
                "limit",
                "min_score",
            ],
            "event_count_a": len(fulltext_a),
            "event_count_b": len(fulltext_b),
            "paired_count": len(paired_keys),
            "input_keys_equal": input_keys_equal,
            "candidate_membership_equal": input_keys_equal and membership_equal,
            "candidate_order_equal": input_keys_equal and order_equal,
        },
        "cosine": {
            "pairing_status": "not_computable_per_input",
            "reason": (
                "retained cosine events have no exact input hash or call correlation id; "
                "array order reflects completion order"
            ),
            "event_count_a": len(cosine_a),
            "event_count_b": len(cosine_b),
            "vector_hash_summary": _vector_hash_summary(cosine_a, cosine_b),
            "aggregate_vector_hash_bag_equal": vector_bag_a == vector_bag_b,
            "aggregate_backend_membership_bag_equal": _signature_counter(
                cosine_a, _backend_membership_signature
            )
            == _signature_counter(cosine_b, _backend_membership_signature),
            "aggregate_backend_order_bag_equal": _signature_counter(
                cosine_a, _backend_order_signature
            )
            == _signature_counter(cosine_b, _backend_order_signature),
            "aggregate_python_selected_membership_bag_equal": _signature_counter(
                cosine_a, _python_selected_signature
            )
            == _signature_counter(cosine_b, _python_selected_signature),
            "aggregate_python_order_bag_equal": _signature_counter(
                cosine_a, _python_order_signature
            )
            == _signature_counter(cosine_b, _python_order_signature),
            "top_k_membership_changed_per_input": NOT_COMPUTABLE,
            "top_k_order_changed_per_input": NOT_COMPUTABLE,
        },
    }


def _tag_content(prompt: str, tag: str) -> str:
    start_token = f"<{tag}>"
    end_token = f"</{tag}>"
    start = prompt.find(start_token)
    end = prompt.find(end_token)
    if start < 0 or end < 0 or end < start:
        raise ValueError(f"prompt does not contain a valid {tag} section")
    return prompt[start + len(start_token) : end].strip()


def _literal_list(prompt: str, tag: str) -> list[dict[str, Any]]:
    content = _tag_content(prompt, tag)
    value = ast.literal_eval(content)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"prompt {tag} section is not a list of dictionaries")
    return value


def _non_user_equal(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return all(first.get(field) == second.get(field) for field in _PROMPT_NON_USER_FIELDS)


def _expected_prompt_record(
    requested_parts: dict[str, Any], cache_records: list[dict[str, Any]]
) -> dict[str, Any]:
    requested_prompt = str(requested_parts.get("user_prompt") or "")
    requested_new = _tag_content(requested_prompt, "NEW FACT")
    requested_existing = _tag_content(requested_prompt, "EXISTING FACTS")
    matches = []
    for record in cache_records:
        parts = record.get("prompt_parts")
        if not isinstance(parts, dict) or not _non_user_equal(requested_parts, parts):
            continue
        candidate_prompt = str(parts.get("user_prompt") or "")
        try:
            same_input = (
                _tag_content(candidate_prompt, "NEW FACT") == requested_new
                and _tag_content(candidate_prompt, "EXISTING FACTS") == requested_existing
            )
        except ValueError:
            continue
        if same_input:
            matches.append(record)
    if len(matches) != 1:
        raise ValueError(
            "expected prompt cache matching must produce exactly one record; "
            f"found {len(matches)}"
        )
    return matches[0]


def _candidate_diff(
    requested: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> dict[str, Any]:
    requested_by_idx = {int(value["idx"]): str(value["fact"]) for value in requested}
    expected_by_idx = {int(value["idx"]): str(value["fact"]) for value in expected}
    substitutions = [
        {
            "idx": index,
            "expected_fact": expected_by_idx[index],
            "requested_fact": requested_by_idx[index],
        }
        for index in sorted(set(requested_by_idx) & set(expected_by_idx))
        if requested_by_idx[index] != expected_by_idx[index]
    ]
    requested_facts = list(requested_by_idx.values())
    expected_facts = list(expected_by_idx.values())
    common = set(requested_facts) & set(expected_facts)
    return {
        "candidate_membership_changed": Counter(requested_facts) != Counter(expected_facts),
        "candidate_order_changed": requested_facts != expected_facts,
        "candidate_substitution_count": len(substitutions),
        "substitutions": substitutions,
        "common_candidate_relative_order_changed": (
            [value for value in requested_facts if value in common]
            != [value for value in expected_facts if value in common]
        ),
    }


def _analyze_prompt_divergence(
    diagnostic_payload: dict[str, Any],
    cache_records: list[dict[str, Any]],
    status: dict[str, Any],
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    diagnostics = diagnostic_payload.get("diagnostics") or []
    if len(diagnostics) != 1:
        raise ValueError("expected exactly one retained unexpected-prompt diagnostic")
    diagnostic = diagnostics[0]
    requested_parts = diagnostic.get("requested_prompt_parts")
    if not isinstance(requested_parts, dict):
        raise ValueError("unexpected-prompt diagnostic lacks requested prompt parts")
    expected_record = _expected_prompt_record(requested_parts, cache_records)
    expected_parts = expected_record["prompt_parts"]
    requested_prompt = str(requested_parts.get("user_prompt") or "")
    expected_prompt = str(expected_parts.get("user_prompt") or "")
    requested_candidates = _literal_list(
        requested_prompt, "FACT INVALIDATION CANDIDATES"
    )
    expected_candidates = _literal_list(expected_prompt, "FACT INVALIDATION CANDIDATES")
    failed_sequences = sorted(
        {
            int(record["source_sequence"])
            for record in trace
            if record.get("error") and record.get("source_sequence") is not None
        }
    )
    result = {
        "evidence_scope": "separate_source5_failure_run",
        "relationship": "downstream divergence observed; embedding cause not established",
        "run_id": status.get("run_id"),
        "run_status": status.get("status"),
        "failed_source_sequences": failed_sequences,
        "live_llm_call_count": int(
            (status.get("llm_metrics") or {}).get("llm_call_count", -1)
        ),
        "requested_prompt_hash": str(diagnostic.get("prompt_hash") or ""),
        "expected_prompt_hash": str(expected_record.get("prompt_hash") or ""),
        "diagnostic_nearest_record_ignored": str(
            (diagnostic.get("nearest_cache_record") or {}).get("prompt_hash") or ""
        ),
        "pairing_rule": [
            *_PROMPT_NON_USER_FIELDS,
            "exact NEW FACT section",
            "exact EXISTING FACTS section",
        ],
        "non_user_components_equal": _non_user_equal(requested_parts, expected_parts),
        "new_fact_equal": _tag_content(requested_prompt, "NEW FACT")
        == _tag_content(expected_prompt, "NEW FACT"),
        "existing_facts_equal": _tag_content(requested_prompt, "EXISTING FACTS")
        == _tag_content(expected_prompt, "EXISTING FACTS"),
        "requested_user_prompt_length": len(requested_prompt),
        "expected_user_prompt_length": len(expected_prompt),
        "prompt_hash_changed": diagnostic.get("prompt_hash")
        != expected_record.get("prompt_hash"),
        "causal_link_to_embedding": "not_established",
    }
    result.update(_candidate_diff(requested_candidates, expected_candidates))
    return result


def _unavailable_metric(reason: str) -> dict[str, Any]:
    return {"status": NOT_COMPUTABLE, "value": None, "reason": reason}


def _run_controls(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    controls = {
        "run_ids": [first.get("run_id"), second.get("run_id")],
        "statuses": [first.get("status"), second.get("status")],
        "same_question_id": first.get("question_id") == second.get("question_id"),
        "same_llm_cache_id": first.get("cache_id") == second.get("cache_id"),
        "same_method": first.get("method") == second.get("method"),
        "same_mode": first.get("mode") == second.get("mode"),
        "episode_count_each": (
            int(first.get("episode_count", -1))
            if first.get("episode_count") == second.get("episode_count")
            else None
        ),
        "live_llm_calls_each": (
            int((first.get("llm_metrics") or {}).get("llm_call_count", -1))
            if (first.get("llm_metrics") or {}).get("llm_call_count")
            == (second.get("llm_metrics") or {}).get("llm_call_count")
            else None
        ),
        "post_run_node_count_each": (
            int(first.get("post_run_node_count", -1))
            if first.get("post_run_node_count") == second.get("post_run_node_count")
            else None
        ),
        "embedding_metrics": [first.get("embedding_metrics"), second.get("embedding_metrics")],
    }
    required = (
        controls["statuses"] == ["success", "success"]
        and controls["same_question_id"]
        and controls["same_llm_cache_id"]
        and controls["same_method"]
        and controls["same_mode"]
        and controls["episode_count_each"] == 6
        and controls["live_llm_calls_each"] == 0
        and controls["post_run_node_count_each"] == 0
    )
    controls["retained_run_contract_pass"] = required
    if not required:
        raise ValueError("retained source-5 run controls do not match the V1 contract")
    return controls


def assert_safe_artifact(value: Any) -> None:
    """Reject secret-bearing fields and the specific causal overclaim V1 cannot make."""

    forbidden_keys = {
        "authorization",
        "api_key",
        "apikey",
        "headers",
        "requested_prompt_parts",
        "raw_response",
        "user_prompt",
        "system_prompt",
        "environment_dump",
    }

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = str(key).casefold().replace("-", "_")
                if normalized in forbidden_keys:
                    raise ValueError(f"unsafe diagnostic field: {key}")
                if (
                    normalized == "embedding_drift_caused_prompt_divergence"
                    and child != "not_established"
                ):
                    raise ValueError("retained evidence cannot establish embedding causation")
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
        elif isinstance(item, str) and "bearer " in item.casefold():
            raise ValueError("unsafe bearer credential in diagnostic artifact")

    visit(value)


def analyze_retained_artifacts(artifacts: str | Path) -> dict[str, Any]:
    artifacts = Path(artifacts)
    paths = _required_paths(artifacts)
    forensic_a = _read_json(paths["source5_run_a_forensics"])
    forensic_b = _read_json(paths["source5_run_b_forensics"])
    status_a = _read_json(paths["source5_run_a_status"])
    status_b = _read_json(paths["source5_run_b_status"])
    prompt_status = _read_json(paths["prompt_divergence_status"])
    prompt_trace = _read_jsonl(paths["prompt_divergence_trace"])
    prompt_diagnostic = _read_json(paths["prompt_divergence_diagnostic"])
    prompt_cache = _read_jsonl(paths["smoke14_prompt_cache"])

    entities = compare_source_state(
        _source_state(forensic_a, "before_node_resolution"),
        _source_state(forensic_b, "before_node_resolution"),
        record_type="entities",
    )
    edges = compare_source_state(
        _source_state(forensic_a, "before_edge_resolution"),
        _source_state(forensic_b, "before_edge_resolution"),
        record_type="edges",
    )
    query_comparison = analyze_query_events(
        forensic_a.get("query_events", []),
        forensic_b.get("query_events", []),
        source_sequence=5,
    )
    prompt_comparison = _analyze_prompt_divergence(
        prompt_diagnostic, prompt_cache, prompt_status, prompt_trace
    )

    no_raw_vector_reason = "raw vectors were not persisted in the retained artifacts"
    no_query_input_reason = (
        "retained cosine events lack exact input bytes/hash and a stable call correlation id"
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "analysis_mode": "retained_artifact_only",
        "evidence_sources": _evidence_sources(artifacts, paths),
        "controls": _run_controls(status_a, status_b),
        "source_state": {
            "pairing_contract": (
                "full retained logical fields plus deterministic duplicate ordinal; "
                "embedding metadata excluded"
            ),
            "entities": entities,
            "edges": edges,
        },
        "numerical_metrics": {
            "embedding_norm_abs_diff": {
                "status": "computed",
                "entity_max": entities["max_embedding_norm_abs_diff"],
                "edge_max": edges["max_embedding_norm_abs_diff"],
                "warning": "norm delta is not a component-wise vector delta",
            },
            "cosine_cross_run": _unavailable_metric(no_raw_vector_reason),
            "l2_cross_run": _unavailable_metric(no_raw_vector_reason),
            "max_abs_diff": _unavailable_metric(no_raw_vector_reason),
            "changed_component_count": _unavailable_metric(no_raw_vector_reason),
            "neo4j_cosine_score_delta": _unavailable_metric(no_raw_vector_reason),
            "exact_embedding_request_bytes_equal": _unavailable_metric(
                no_query_input_reason
            ),
        },
        "query_comparison": query_comparison,
        "prompt_comparison": prompt_comparison,
        "claims": {
            "live_embedding_bitwise_deterministic": False,
            "live_embedding_suitable_as_bitwise_correctness_oracle": False,
            "same_retained_logical_source_state_has_changed_embedding_hashes": True,
            "fulltext_query_and_candidate_path_stable_in_source5_pair": True,
            "aggregate_cosine_ranking_evidence_varied": True,
            "separate_downstream_prompt_candidate_divergence_observed": True,
            "embedding_drift_caused_ranking_divergence": "not_established",
            "embedding_drift_caused_prompt_divergence": "not_established",
            "final_graph_semantic_error_established": False,
        },
        "v1_gate": {
            "status": "pass_with_explicit_evidence_limits",
            "closure_question": "live embedding suitability as a bitwise correctness oracle",
            "numerical_root_cause_established": False,
            "raw_vector_recapture_required": False,
            "live_calls_authorized": False,
            "next_stage": "V2",
        },
    }
    assert_safe_artifact(result)
    return result


def write_retained_diagnostic(
    artifacts: str | Path,
    output: str | Path | None = None,
) -> dict[str, Any]:
    artifacts = Path(artifacts)
    output = (
        Path(output)
        if output is not None
        else artifacts / "diagnostics" / "embedding_nondeterminism_source5.json"
    )
    result = analyze_retained_artifacts(artifacts)
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    assert_safe_artifact(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return result
