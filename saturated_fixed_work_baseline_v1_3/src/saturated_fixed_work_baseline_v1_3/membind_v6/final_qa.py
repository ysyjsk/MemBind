"""Fail-closed contract for read-only final QA of sealed V6 candidates.

This module is deliberately provider-free.  It validates immutable V6 build
artifacts, binds their episode mapping to the frozen formal baseline, and
reduces live Reader/Judge rows into one reviewer-safe verdict.  The live CLI
owns all network I/O and writes only to a fresh sidecar root.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


EXPECTED_HISTORY_ID = "6071bd76"
EXPECTED_SOURCE_COUNT = 46
FORMAL_BASELINE_SEAL_SHA256 = (
    "695cb71c9b6e305ad9c3e26b90c1b9d9487c54e32200fed65e40ff9a1205e8c2"
)
_SHA256_LENGTH = 64


class V6FinalQAError(ValueError):
    """The V6 final-QA authority contract is malformed or incomplete."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise V6FinalQAError(f"artifact is unreadable: {path}") from exc
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V6FinalQAError(f"artifact is missing or invalid: {path}") from exc
    if not isinstance(value, dict):
        raise V6FinalQAError(f"artifact must be a JSON object: {path}")
    return value


def graph_namespace(graph: Mapping[str, Any]) -> str:
    """Return the sole nonempty namespace represented by a canonical graph."""

    if not isinstance(graph, Mapping):
        raise V6FinalQAError("canonical graph is invalid")
    namespaces: set[str] = set()
    for field in ("entities", "edges", "episodes"):
        rows = graph.get(field)
        if not isinstance(rows, list):
            raise V6FinalQAError(f"canonical graph {field} are invalid")
        for row in rows:
            if not isinstance(row, Mapping):
                raise V6FinalQAError(f"canonical graph {field} row is invalid")
            value = row.get("group_id")
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise V6FinalQAError("canonical graph namespace is invalid")
                namespaces.add(value)
    if len(namespaces) != 1:
        raise V6FinalQAError(
            f"canonical graph namespace is not unique: {sorted(namespaces)}"
        )
    return next(iter(namespaces))


def canonical_episode_mapping(
    graph: Mapping[str, Any], *, expected_source_count: int = EXPECTED_SOURCE_COUNT
) -> tuple[dict[str, Any], ...]:
    """Validate and sort the frozen source/session/hash mapping."""

    rows = graph.get("episodes") if isinstance(graph, Mapping) else None
    if not isinstance(rows, list) or len(rows) != expected_source_count:
        observed = len(rows) if isinstance(rows, list) else -1
        raise V6FinalQAError(
            f"canonical episode coverage is incomplete: {observed}/{expected_source_count}"
        )
    normalized: list[dict[str, Any]] = []
    sequences: set[int] = set()
    sessions: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise V6FinalQAError("canonical episode row is invalid")
        sequence = row.get("source_sequence")
        session_id = row.get("session_id")
        source_hash = row.get("source_hash")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or not isinstance(session_id, str)
            or not session_id
            or not isinstance(source_hash, str)
            or len(source_hash) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in source_hash)
            or sequence in sequences
            or session_id in sessions
        ):
            raise V6FinalQAError("canonical episode identity is invalid")
        sequences.add(sequence)
        sessions.add(session_id)
        normalized.append(
            {
                "source_sequence": sequence,
                "session_id": session_id,
                "source_hash": source_hash,
            }
        )
    expected_sequences = set(range(expected_source_count))
    if sequences != expected_sequences:
        raise V6FinalQAError("canonical episode source sequence coverage is invalid")
    return tuple(sorted(normalized, key=lambda item: int(item["source_sequence"])))


def _candidate(
    root: Path,
    *,
    baseline_mapping: tuple[dict[str, Any], ...],
    expected_history_id: str,
    expected_source_count: int,
) -> dict[str, Any]:
    resolved = root.resolve()
    seal_path = resolved / "seal.json"
    manifest_path = resolved / "manifest.json"
    proof_path = resolved / "proof.json"
    graph_path = resolved / f"histories/{expected_history_id}/canonical_graph.json"
    seal = read_json(seal_path)
    manifest = read_json(manifest_path)
    proof = read_json(proof_path)
    graph = read_json(graph_path)

    if (
        seal.get("status") != "V6_PROBE_SEALED"
        or seal.get("history_id") != expected_history_id
        or seal.get("policy") != "v6"
        or seal.get("method") != "V6_REQUEST_STABILITY_PROBE"
        or seal.get("source_count") != expected_source_count
        or seal.get("durable_frontier") != expected_source_count - 1
    ):
        raise V6FinalQAError(f"candidate seal is not complete V6: {resolved}")
    baseline_reference = manifest.get("baseline_reference")
    if (
        manifest.get("status") != "PASS"
        or manifest.get("policy") != "v6"
        or manifest.get("method") != "V6_REQUEST_STABILITY_PROBE"
        or manifest.get("native_graphiti_path") != "Graphiti.add_episode"
        or not isinstance(baseline_reference, Mapping)
        or baseline_reference.get("formal_run_seal_sha256")
        != FORMAL_BASELINE_SEAL_SHA256
    ):
        raise V6FinalQAError(f"candidate manifest authority is invalid: {resolved}")

    frontier = proof.get("frontier")
    provider = proof.get("provider")
    replay = proof.get("replay")
    if (
        not isinstance(frontier, Mapping)
        or frontier.get("status") != "PASS"
        or frontier.get("durable_frontier") != expected_source_count - 1
        or frontier.get("publication_count") != expected_source_count
    ):
        raise V6FinalQAError(f"candidate frontier proof is invalid: {resolved}")
    if not isinstance(provider, Mapping) or provider.get("status") != "PASS":
        raise V6FinalQAError(f"candidate provider proof is invalid: {resolved}")
    capacity = provider.get("capacity")
    if (
        isinstance(capacity, bool)
        or not isinstance(capacity, int)
        or capacity < 1
        or not isinstance(provider.get("max_outstanding"), int)
        or not isinstance(provider.get("max_future_outstanding"), int)
        or int(provider["max_outstanding"]) > capacity
        or int(provider["max_future_outstanding"]) > capacity - 1
    ):
        raise V6FinalQAError(f"candidate provider capacity proof is invalid: {resolved}")
    if (
        not isinstance(replay, Mapping)
        or replay.get("status") != "PASS"
        or replay.get("logical_captured") != replay.get("logical_consumed")
    ):
        raise V6FinalQAError(f"candidate exact replay proof is invalid: {resolved}")

    episode_mapping = canonical_episode_mapping(
        graph, expected_source_count=expected_source_count
    )
    if episode_mapping != baseline_mapping:
        raise V6FinalQAError(f"candidate episode mapping differs from baseline: {resolved}")
    namespace = graph_namespace(graph)
    return {
        "candidate_id": resolved.name,
        "root": str(resolved),
        "history_id": expected_history_id,
        "namespace": namespace,
        "source_count": expected_source_count,
        "canonical_graph_path": str(graph_path),
        "episode_mapping_sha256": payload_sha256(episode_mapping),
        "input_artifact_sha256": {
            "seal": file_sha256(seal_path),
            "manifest": file_sha256(manifest_path),
            "proof": file_sha256(proof_path),
            "canonical_graph": file_sha256(graph_path),
        },
        "tree_sha256_before": tree_sha256(resolved),
    }


def validate_candidates(
    *,
    candidate_roots: Sequence[str | Path],
    baseline_graph_path: str | Path,
    expected_history_id: str = EXPECTED_HISTORY_ID,
    expected_source_count: int = EXPECTED_SOURCE_COUNT,
) -> list[dict[str, Any]]:
    """Validate two or more independently sealed V6 repetitions."""

    if isinstance(candidate_roots, (str, bytes)) or len(candidate_roots) < 2:
        raise V6FinalQAError("at least two candidate repetitions are required")
    resolved_roots = [Path(root).resolve() for root in candidate_roots]
    if len(set(resolved_roots)) != len(resolved_roots):
        raise V6FinalQAError("candidate roots must be unique")
    baseline_graph = read_json(Path(baseline_graph_path).resolve())
    baseline_mapping = canonical_episode_mapping(
        baseline_graph, expected_source_count=expected_source_count
    )
    candidates = [
        _candidate(
            root,
            baseline_mapping=baseline_mapping,
            expected_history_id=expected_history_id,
            expected_source_count=expected_source_count,
        )
        for root in resolved_roots
    ]
    namespaces = [str(item["namespace"]) for item in candidates]
    if len(set(namespaces)) != len(namespaces):
        raise V6FinalQAError("candidate repetitions must use distinct namespaces")
    mapping_hashes = {str(item["episode_mapping_sha256"]) for item in candidates}
    if len(mapping_hashes) != 1:
        raise V6FinalQAError("candidate episode mappings are inconsistent")
    return candidates


def gold_blind_retrieval_arguments(
    *, query: str, namespace: str, episode_uuid_to_session_id: Mapping[str, str]
) -> dict[str, Any]:
    """Build the complete label-free argument surface for retrieval."""

    if not isinstance(query, str) or not query.strip():
        raise V6FinalQAError("retrieval query is invalid")
    if not isinstance(namespace, str) or not namespace.strip():
        raise V6FinalQAError("retrieval namespace is invalid")
    if not isinstance(episode_uuid_to_session_id, Mapping):
        raise V6FinalQAError("retrieval episode mapping is invalid")
    mapping = {
        str(uuid): str(session_id)
        for uuid, session_id in episode_uuid_to_session_id.items()
    }
    if not mapping or any(not uuid or not session_id for uuid, session_id in mapping.items()):
        raise V6FinalQAError("retrieval episode mapping is invalid")
    return {
        "query": query,
        "namespace": namespace,
        "episode_uuid_to_session_id": mapping,
    }


def retrieval_identity_sha256(
    *,
    ranked_session_ids: Sequence[str],
    query: str,
    search_config_sha256: str,
) -> str:
    """Bind gold-blind retrieval output without run-specific episode UUIDs."""

    ranked = list(ranked_session_ids) if not isinstance(ranked_session_ids, (str, bytes)) else []
    if (
        not ranked
        or any(not isinstance(value, str) or not value for value in ranked)
        or len(set(ranked)) != len(ranked)
        or not isinstance(query, str)
        or not query.strip()
        or not isinstance(search_config_sha256, str)
        or len(search_config_sha256) != _SHA256_LENGTH
    ):
        raise V6FinalQAError("retrieval identity is invalid")
    return payload_sha256(
        {
            "ranked_session_ids": ranked,
            "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "search_config_sha256": search_config_sha256,
        }
    )


def validate_persisted_episode_rows(
    *,
    records: Sequence[Mapping[str, Any]],
    namespace: str,
    expected_name_to_session_id: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Bind every persisted Episodic row to the frozen source identity."""

    if not isinstance(namespace, str) or not namespace:
        raise V6FinalQAError("persisted episode namespace is invalid")
    if not isinstance(expected_name_to_session_id, Mapping):
        raise V6FinalQAError("persisted episode expectation is invalid")
    expected = {
        str(name): str(session_id)
        for name, session_id in expected_name_to_session_id.items()
    }
    if not expected or any(not name or not session_id for name, session_id in expected.items()):
        raise V6FinalQAError("persisted episode expectation is invalid")
    values = list(records) if not isinstance(records, (str, bytes)) else []
    if len(values) != len(expected):
        raise V6FinalQAError(
            f"persisted episode coverage is incomplete: {len(values)}/{len(expected)}"
        )
    uuid_to_session: dict[str, str] = {}
    rows: dict[str, dict[str, str]] = {}
    seen_names: set[str] = set()
    for record in values:
        if not isinstance(record, Mapping):
            raise V6FinalQAError("persisted episode row is invalid")
        uuid = record.get("uuid")
        name = record.get("name")
        group_id = record.get("group_id")
        content = record.get("content")
        if (
            not isinstance(uuid, str)
            or not uuid
            or not isinstance(name, str)
            or name not in expected
            or not isinstance(group_id, str)
            or group_id != namespace
            or not isinstance(content, str)
            or not content.strip()
            or uuid in rows
            or name in seen_names
        ):
            raise V6FinalQAError("persisted episode identity or content is invalid")
        seen_names.add(name)
        uuid_to_session[uuid] = expected[name]
        rows[uuid] = {"name": name, "content": content}
    if seen_names != set(expected):
        raise V6FinalQAError(
            f"persisted episode coverage is incomplete: {len(seen_names)}/{len(expected)}"
        )
    return uuid_to_session, rows


def _lane_status(row: Mapping[str, Any], lane: str) -> str | None:
    value = row.get(lane)
    evaluation = value.get("answer_evaluation") if isinstance(value, Mapping) else None
    if not isinstance(evaluation, Mapping):
        return None
    status = evaluation.get("status")
    authority = evaluation.get("semantic_authority")
    if status not in {"PASS", "FAIL"} or authority != "OFFICIAL_LONGMEMEVAL_JUDGE":
        return None
    return str(status)


def final_qa_verdict(
    *, rows: Sequence[Mapping[str, Any]], runtime_evidence: Mapping[str, Any]
) -> dict[str, Any]:
    """Reduce complete layered QA rows into one fail-closed V6 verdict."""

    values = list(rows) if not isinstance(rows, (str, bytes)) else []
    read_only = (
        isinstance(runtime_evidence, Mapping)
        and runtime_evidence.get("construction_calls") == 0
        and runtime_evidence.get("graph_writes") == 0
        and runtime_evidence.get("candidate_roots_unchanged") is True
    )
    candidate_ids = [str(row.get("candidate_root") or "") for row in values]
    complete_pair = (
        len(values) >= 2
        and len(set(candidate_ids)) == len(values)
        and all(candidate_ids)
    )
    headline_statuses = [_lane_status(row, "headline") for row in values]
    ablation_statuses = [_lane_status(row, "ablation") for row in values]
    recall_complete = all(
        isinstance(row.get("session_recall_posthoc"), Mapping)
        and row["session_recall_posthoc"].get("recall_at_10") == 1.0
        for row in values
    )
    determinate = (
        read_only
        and complete_pair
        and recall_complete
        and all(status in {"PASS", "FAIL"} for status in headline_statuses)
        and all(status in {"PASS", "FAIL"} for status in ablation_statuses)
    )
    if not determinate:
        verdict = "QA_INDETERMINATE"
    elif all(status == "PASS" for status in headline_statuses):
        verdict = "VALID_QA_PASS"
    else:
        verdict = "VALID_QA_FAIL"
    stable = (
        determinate
        and len(set(headline_statuses)) == 1
        and len(set(ablation_statuses)) == 1
    )
    answers = [
        str(row.get("headline", {}).get("reader_answer") or "")
        if isinstance(row.get("headline"), Mapping)
        else ""
        for row in values
    ]
    retrieval_identities = [
        str(row.get("retrieval", {}).get("retrieval_identity_sha256") or "")
        if isinstance(row.get("retrieval"), Mapping)
        else ""
        for row in values
    ]
    return {
        "schema_version": "membind.v6.final-qa-verdict.v1",
        "verdict": verdict,
        "quality_claim": verdict == "VALID_QA_PASS",
        "authority": "OFFICIAL_LONGMEMEVAL_JSON_CHAIN_OF_NOTE_AND_PINNED_JUDGE",
        "candidate_count": len(values),
        "headline_pass_count": sum(status == "PASS" for status in headline_statuses),
        "headline_statuses": headline_statuses,
        "ablation_statuses": ablation_statuses,
        "repetition_stable": stable,
        "complete_posthoc_recall": recall_complete,
        "answer_divergence": len(set(answers)) > 1,
        "retrieval_identity_divergence": len(set(retrieval_identities)) > 1,
        "read_only_evidence_valid": read_only,
        "runtime_evidence": dict(runtime_evidence),
    }


def tree_sha256(root: str | Path) -> str:
    """Hash every regular file by relative path and content."""

    resolved = Path(root).resolve()
    if not resolved.is_dir():
        raise V6FinalQAError(f"candidate root is not a directory: {resolved}")
    rows: list[dict[str, Any]] = []
    for path in sorted(resolved.rglob("*")):
        if path.is_symlink():
            raise V6FinalQAError(f"candidate root contains a symlink: {path}")
        if path.is_file():
            rows.append(
                {
                    "relative_path": path.relative_to(resolved).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": file_sha256(path),
                }
            )
    if not rows:
        raise V6FinalQAError(f"candidate root contains no artifacts: {resolved}")
    return payload_sha256(rows)


def create_fresh_output_root(path: str | Path) -> Path:
    """Create a new append-only sidecar root without accepting reuse."""

    resolved = Path(path).resolve()
    if resolved.exists():
        raise V6FinalQAError(f"QA output root must be fresh: {resolved}")
    try:
        resolved.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise V6FinalQAError(f"cannot create fresh QA output root: {resolved}") from exc
    return resolved


def write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    """Write one hash-bound JSON artifact without replacing existing data."""

    if path.exists():
        raise V6FinalQAError(f"QA artifact already exists: {path}")
    body = dict(value)
    body["payload_sha256"] = payload_sha256(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(body, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
    except OSError as exc:
        raise V6FinalQAError(f"cannot write QA artifact: {path}") from exc


__all__ = [
    "EXPECTED_HISTORY_ID",
    "EXPECTED_SOURCE_COUNT",
    "FORMAL_BASELINE_SEAL_SHA256",
    "V6FinalQAError",
    "canonical_episode_mapping",
    "create_fresh_output_root",
    "file_sha256",
    "final_qa_verdict",
    "gold_blind_retrieval_arguments",
    "graph_namespace",
    "payload_sha256",
    "read_json",
    "retrieval_identity_sha256",
    "tree_sha256",
    "validate_candidates",
    "validate_persisted_episode_rows",
    "write_new_json",
]
