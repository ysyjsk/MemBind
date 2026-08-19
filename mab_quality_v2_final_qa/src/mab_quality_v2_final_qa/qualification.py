"""Fail-closed dataset qualification that reports all context defects."""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import canonical_sha256
from .dataset_adapter import DatasetMappingError, MABDatasetAdapter


def _record_source(record: Mapping[str, Any]) -> str:
    metadata = record.get("metadata", {})
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    return str(record.get("source", metadata.get("source", "UNKNOWN")))


def qualify_records(
    records: Sequence[Mapping[str, Any]],
    *,
    source: str | None = None,
    dataset_revision: str = "UNPINNED",
) -> dict[str, Any]:
    """Qualify every record without letting one defect hide later defects.

    No accepted context is authorized for live use when any selected record is
    rejected.  The caller must pin a corrected upstream dataset revision or a
    pre-declared, versioned inventory before proceeding.
    """

    selected = 0
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            rejected.append(
                {"record_index": index, "failure": "record is not an object"}
            )
            selected += 1
            continue
        record_source = _record_source(record)
        if source:
            if not fnmatch.fnmatch(record_source, source):
                continue
        selected += 1
        try:
            adapter = MABDatasetAdapter.from_records(
                [record], source=None, dataset_revision=dataset_revision
            )
            context = adapter.contexts[0]
            accepted.append(
                {
                    "record_index": index,
                    "context_id": context.context_id,
                    "context_sha256": context.context_sha256,
                    "session_count": len(context.sessions),
                    "qa_count": len(context.qa_items),
                }
            )
        except DatasetMappingError as error:
            rejected.append(
                {
                    "record_index": index,
                    "source": record_source,
                    "failure": str(error),
                }
            )
    if selected == 0:
        decision = "STOP_DATASET_MAPPING_UNQUALIFIED"
        rejected.append(
            {"record_index": None, "failure": "source filter selected no records"}
        )
    elif rejected:
        decision = "STOP_DATASET_MAPPING_UNQUALIFIED"
    else:
        decision = "PASS_DATASET_MAPPING_QUALIFIED"
    body: dict[str, Any] = {
        "schema_version": "mab-quality-v2-final-qa.dataset-qualification.v1",
        "dataset_revision": dataset_revision,
        "source_filter": source,
        "selected_record_count": selected,
        "accepted_context_count": len(accepted),
        "rejected_context_count": len(rejected),
        "accepted_qa_count": sum(item["qa_count"] for item in accepted),
        "accepted": accepted,
        "rejected": rejected,
        "decision": decision,
        "live_authorized": decision == "PASS_DATASET_MAPPING_QUALIFIED",
    }
    body["payload_sha256"] = canonical_sha256(body)
    return body


def qualify_declared_inventory(
    records: Sequence[Mapping[str, Any]],
    *,
    source: str,
    dataset_revision: str,
    included_record_indices: Sequence[int],
    excluded_failures: Mapping[int, str],
) -> dict[str, Any]:
    """Freeze an explicit valid-context inventory before any quality call.

    Every source-selected row must be included or excluded.  An exclusion is
    accepted only when the adapter independently reproduces its exact mapping
    failure, preventing outcome-based removal of otherwise valid contexts.
    """

    values = list(records)
    selected_indices = {
        index
        for index, record in enumerate(values)
        if isinstance(record, Mapping)
        and fnmatch.fnmatch(_record_source(record), source)
    }
    included = tuple(int(value) for value in included_record_indices)
    excluded = {int(key): str(value) for key, value in excluded_failures.items()}
    if (
        len(set(included)) != len(included)
        or set(included).intersection(excluded)
        or set(included).union(excluded) != selected_indices
    ):
        raise ValueError("DECLARED_INVENTORY_PARTITION_INVALID")

    contexts = []
    context_rows: list[dict[str, Any]] = []
    for index in included:
        try:
            adapter = MABDatasetAdapter.from_records(
                [values[index]], dataset_revision=dataset_revision
            )
        except (IndexError, DatasetMappingError) as error:
            raise ValueError(
                f"INCLUDED_CONTEXT_UNQUALIFIED:{index}:{error}"
            ) from None
        context = adapter.contexts[0]
        contexts.append(context)
        context_rows.append(
            {
                "record_index": index,
                "context_id": context.context_id,
                "context_sha256": context.context_sha256,
                "session_count": len(context.sessions),
                "qa_count": len(context.qa_items),
            }
        )

    exclusion_rows: list[dict[str, Any]] = []
    for index, expected_failure in sorted(excluded.items()):
        try:
            MABDatasetAdapter.from_records(
                [values[index]], dataset_revision=dataset_revision
            )
        except (IndexError, DatasetMappingError) as error:
            observed = str(error)
        else:
            raise ValueError(f"DECLARED_EXCLUSION_NOT_REPRODUCED:{index}")
        if observed != expected_failure:
            raise ValueError(
                f"DECLARED_EXCLUSION_FAILURE_MISMATCH:{index}:{observed}"
            )
        exclusion_rows.append(
            {
                "record_index": index,
                "failure": observed,
                "disposition": "WHOLE_CONTEXT_EXCLUDED_BEFORE_QUALITY",
            }
        )

    question_inventory = [
        {
            "context_id": context.context_id,
            "qa_pair_id": qa.qa_pair_id,
            "question_id": qa.question_id,
            "qa_identity_sha256": canonical_sha256(qa.public_dict()),
        }
        for context in contexts
        for qa in context.qa_items
    ]
    body: dict[str, Any] = {
        "schema_version": (
            "mab-quality-v2-final-qa.declared-dataset-inventory.v1"
        ),
        "dataset_revision": dataset_revision,
        "source_filter": source,
        "selection_timing": "BEFORE_ANY_QUALITY_RESULT",
        "selection_policy": "ALL_AND_ONLY_CONTEXTS_WITH_EXACT_GOLD_MAPPING",
        "included_record_indices": list(included),
        "included_context_count": len(contexts),
        "included_qa_count": len(question_inventory),
        "included": context_rows,
        "excluded_context_count": len(exclusion_rows),
        "excluded": exclusion_rows,
        "question_inventory_sha256": canonical_sha256(question_inventory),
        "decision": "PASS_DECLARED_INVENTORY_QUALIFIED",
        "live_authorized": True,
    }
    body["payload_sha256"] = canonical_sha256(body)
    return body


__all__ = ["qualify_declared_inventory", "qualify_records"]
