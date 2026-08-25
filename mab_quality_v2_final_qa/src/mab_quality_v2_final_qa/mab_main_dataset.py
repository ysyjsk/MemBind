"""Pinned MemoryAgentBench main-component authority and projections."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import MABContext, canonical_sha256
from .dataset_adapter import (
    KNOWN_PARTIAL_GOLD_QUESTION_ID,
    MABDatasetAdapter,
)
from .workload_contract import (
    EpisodeInput,
    WorkloadContractError,
    WorkloadManifest,
    canonical_episode_body,
    stable_episode_id,
)


HF_DATASET = "ai-hyz/MemoryAgentBench"
DATASET_REVISION = "7ea066982b140a19337e17e60d45d4076e042faf"
PINNED_ADAPTER_REVISION = f"hf:{HF_DATASET}@{DATASET_REVISION}"
SOURCE_FILTER = "longmemeval_s*"
EXPECTED_LOCAL_FILE_SHA256 = "97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8"
EXPECTED_SESSION_COUNTS = (111, 107, 116, 111, 110)
EXPECTED_QA_PER_CONTEXT = 60
EXPECTED_TOTAL_SESSIONS = 555
EXPECTED_TOTAL_QA = 300
EXPECTED_QA_TYPE_COUNTS = {
    "knowledge-update": 45,
    "multi-session": 75,
    "single-session-assistant": 30,
    "single-session-preference": 30,
    "single-session-user": 45,
    "temporal-reasoning": 75,
}


class MainDatasetAuthorityError(ValueError):
    """The pinned main component cannot satisfy formal authority."""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "official_5_contexts.json"


def load_main_contexts(path: str | Path | None = None) -> tuple[MABContext, ...]:
    dataset_path = Path(path) if path is not None else _default_path()
    adapter = MABDatasetAdapter.from_file(
        dataset_path,
        source=SOURCE_FILTER,
        dataset_revision=PINNED_ADAPTER_REVISION,
    )
    return adapter.contexts


def _authority_payload(
    *, path: Path, contexts: Sequence[MABContext], file_sha256: str
) -> dict[str, Any]:
    type_counts = Counter(
        item.question_type for context in contexts for item in context.qa_items
    )
    partial = sorted(
        item.question_id
        for context in contexts
        for item in context.qa_items
        if item.gold_mapping_status == "PARTIAL_GOLD_MAPPING"
    )
    return {
        "schema_version": "membind.v1.3.dataset-authority.v1",
        "benchmark": "MemoryAgentBench",
        "task": "Accurate Retrieval",
        "hf_dataset": HF_DATASET,
        "revision": DATASET_REVISION,
        "source_filter": SOURCE_FILTER,
        "local_file": str(path.resolve()),
        "local_file_sha256": file_sha256,
        "context_count": len(contexts),
        "context_ids": [context.context_id for context in contexts],
        "session_counts": [len(context.sessions) for context in contexts],
        "total_sessions": sum(len(context.sessions) for context in contexts),
        "qa_per_context": EXPECTED_QA_PER_CONTEXT,
        "qa_counts": [len(context.qa_items) for context in contexts],
        "total_qa": sum(len(context.qa_items) for context in contexts),
        "question_type_counts": dict(sorted(type_counts.items())),
        "partial_gold_mapping_question_ids": partial,
        "authority_status": "FULL_OFFICIAL_COMPONENT",
    }


def build_authority(path: str | Path | None = None) -> dict[str, Any]:
    dataset_path = Path(path) if path is not None else _default_path()
    if not dataset_path.is_file():
        raise MainDatasetAuthorityError(f"dataset file is missing: {dataset_path}")
    file_sha256 = _file_sha256(dataset_path)
    contexts = load_main_contexts(dataset_path)
    payload = _authority_payload(path=dataset_path, contexts=contexts, file_sha256=file_sha256)
    if file_sha256 != EXPECTED_LOCAL_FILE_SHA256:
        raise MainDatasetAuthorityError(
            f"local dataset hash mismatch: {file_sha256} != {EXPECTED_LOCAL_FILE_SHA256}"
        )
    checks = {
        "context_count": payload["context_count"] == 5,
        "session_counts": tuple(payload["session_counts"]) == EXPECTED_SESSION_COUNTS,
        "total_sessions": payload["total_sessions"] == EXPECTED_TOTAL_SESSIONS,
        "qa_counts": payload["qa_counts"] == [EXPECTED_QA_PER_CONTEXT] * 5,
        "total_qa": payload["total_qa"] == EXPECTED_TOTAL_QA,
        "question_type_counts": payload["question_type_counts"] == EXPECTED_QA_TYPE_COUNTS,
        "source_filter": all(context.context_id for context in contexts),
        "known_partial_mapping": payload["partial_gold_mapping_question_ids"]
        == [KNOWN_PARTIAL_GOLD_QUESTION_ID],
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise MainDatasetAuthorityError("authority checks failed: " + ", ".join(failed))
    payload["authority_checks"] = checks
    payload["authority_sha256"] = canonical_sha256(payload)
    # Context objects are intentionally available to callers but are never
    # written into the JSON authority artifact.
    payload["contexts"] = tuple(contexts)
    return payload


def authority_artifact(authority: Mapping[str, Any]) -> dict[str, Any]:
    artifact = {key: value for key, value in authority.items() if key != "contexts"}
    artifact["authority_sha256"] = canonical_sha256(
        {key: value for key, value in artifact.items() if key != "authority_sha256"}
    )
    return artifact


def build_episode_inputs(context: MABContext) -> tuple[EpisodeInput, ...]:
    if not isinstance(context, MABContext):
        raise TypeError("context must be an MABContext")
    episodes: list[EpisodeInput] = []
    for session in context.sessions:
        episodes.append(
            EpisodeInput(
                context_id=context.context_id,
                source_sequence=session.source_sequence,
                episode_id=stable_episode_id(
                    dataset_revision=DATASET_REVISION,
                    context_id=context.context_id,
                    source_sequence=session.source_sequence,
                ),
                reference_time=session.timestamp,
                body=canonical_episode_body(session),
                arrival_offset_s=0.0,
            )
        )
    return tuple(episodes)


def build_workload_manifest(
    context: MABContext,
    authority: Mapping[str, Any],
    *,
    scope: str = "FORMAL",
) -> WorkloadManifest:
    episodes = build_episode_inputs(context)
    return WorkloadManifest.from_episodes(
        context_id=context.context_id,
        episodes=episodes,
        dataset_revision=DATASET_REVISION,
        dataset_file_sha256=str(authority["local_file_sha256"]),
        scope=scope,
        expected_episode_count=len(context.sessions),
    )


def build_qa_manifest(context: MABContext, *, scope: str = "FULL") -> list[dict[str, Any]]:
    if scope not in {"FULL", "SMOKE"}:
        raise MainDatasetAuthorityError("QA scope must be FULL or SMOKE")
    items = list(context.qa_items)
    if scope == "SMOKE":
        by_type: dict[str, Any] = {}
        for item in items:
            by_type.setdefault(item.question_type, item)
        if set(by_type) != set(EXPECTED_QA_TYPE_COUNTS):
            raise MainDatasetAuthorityError("smoke QA type inventory is incomplete")
        items = [by_type[name] for name in sorted(by_type)]
    result: list[dict[str, Any]] = []
    for item in items:
        row = {
            "context_id": context.context_id,
            "qa_pair_id": item.qa_pair_id,
            "question_id": item.question_id,
            "question": item.question,
            "question_date": item.question_date,
            "question_type": item.question_type,
            "question_sha256": canonical_sha256(item.question),
            "reference_answer_sha256": canonical_sha256(list(item.reference_answers)),
            "gold_session_ids": list(item.gold_session_ids),
            "gold_mapping_status": item.gold_mapping_status,
            "scope": scope,
        }
        row["qa_identity_sha256"] = canonical_sha256(
            {key: row[key] for key in ("context_id", "qa_pair_id", "question_id", "question_date", "question_type", "question_sha256", "reference_answer_sha256")}
        )
        result.append(row)
    return result


__all__ = [
    "DATASET_REVISION",
    "EXPECTED_LOCAL_FILE_SHA256",
    "EXPECTED_QA_TYPE_COUNTS",
    "EXPECTED_SESSION_COUNTS",
    "HF_DATASET",
    "KNOWN_PARTIAL_GOLD_QUESTION_ID",
    "MainDatasetAuthorityError",
    "PINNED_ADAPTER_REVISION",
    "SOURCE_FILTER",
    "authority_artifact",
    "build_authority",
    "build_episode_inputs",
    "build_qa_manifest",
    "build_workload_manifest",
    "load_main_contexts",
]
