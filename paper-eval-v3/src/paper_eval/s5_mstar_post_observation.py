"""Cross-bind successful M* publication evidence to a Native observation.

The M* attempt ledger proves source-order publication, while the separate
publication journal proves the external commit boundary and the generic Native
observer proves what Neo4j contains.  This module joins those three public
surfaces without retaining run IDs, namespaces, graph content, or commit IDs.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy

from .artifacts import payload_sha256
from .s5_native_post_observation import verify_s5_native_post_observation


SCHEMA = "membind.paper-eval-v3.s5-mstar-post-observation.v1"
_RUN_ID = re.compile(r"^s5-mstar-[0-9]{8}-[0-9]{3}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = {
    "schema_version",
    "method",
    "status",
    "run_id_sha256",
    "source_manifest_sha256",
    "attempt_publication_manifest_sha256",
    "operation_manifest_sha256",
    "journal_events_sha256",
    "native_observation_sha256",
    "native_observation",
    "summary",
    "post_observation_sha256",
}
_SUMMARY_FIELDS = {
    "expected_source_count",
    "attempt_publication_count",
    "journal_intent_count",
    "journal_commit_count",
    "journal_publication_count",
    "journal_recovered_publication_count",
    "global_violation_total",
}


class S5MStarPostObservationError(ValueError):
    """M* publication evidence or its independent observation is inconsistent."""


def _fail(code: str) -> S5MStarPostObservationError:
    return S5MStarPostObservationError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _sources(value: object) -> list[dict[str, object]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("source_inventory_invalid")
    selected: list[dict[str, object]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise _fail("source_inventory_invalid")
        row = dict(raw)
        if (
            set(row) != {"source_sequence", "source_sha256"}
            or row.get("source_sequence") != index
        ):
            raise _fail("source_inventory_invalid")
        _sha(row.get("source_sha256"), "source_inventory_invalid")
        selected.append(row)
    if len(selected) != 49 or len({row["source_sha256"] for row in selected}) != 49:
        raise _fail("source_inventory_invalid")
    return selected


def _publication_projection(value: object) -> list[dict[str, object]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("attempt_publication_invalid")
    selected: list[dict[str, object]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or raw.get("event_type") != "publication":
            raise _fail("attempt_publication_invalid")
        source = raw.get("source_sequence")
        if isinstance(source, bool) or not isinstance(source, int):
            raise _fail("attempt_publication_invalid")
        selected.append(
            {
                "source_sequence": source,
                "source_sha256": _sha(
                    raw.get("source_sha256"), "attempt_publication_invalid"
                ),
            }
        )
    return selected


def _operation_manifest(run_id: str, sources: Sequence[Mapping[str, object]]) -> list[dict[str, str]]:
    return [
        {
            "operation_id": payload_sha256(
                {
                    "run_id": run_id,
                    "source_sequence": row["source_sequence"],
                    "source_sha256": row["source_sha256"],
                }
            ),
            "source_sha256": str(row["source_sha256"]),
        }
        for row in sources
    ]


def _journal_summary(
    value: object,
    *,
    operations: Sequence[Mapping[str, str]],
) -> tuple[dict[str, int], str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("journal_events_invalid")
    events = [deepcopy(dict(row)) if isinstance(row, Mapping) else {} for row in value]
    if [row.get("event_sequence") for row in events] != list(range(len(events))):
        raise _fail("journal_event_sequence_invalid")

    expected: list[dict[str, object]] = []
    for operation in operations:
        expected.append(
            {
                "event_type": "intent",
                "operation_id": operation["operation_id"],
                "source_sha256": operation["source_sha256"],
            }
        )
    for operation in operations:
        expected.extend(
            (
                {
                    "event_type": "commit",
                    "operation_id": operation["operation_id"],
                    "source_sha256": operation["source_sha256"],
                },
                {
                    "event_type": "publication",
                    "operation_id": operation["operation_id"],
                    "source_sha256": operation["source_sha256"],
                },
            )
        )
    if len(events) != len(expected):
        raise _fail("journal_coverage_invalid")

    recovered = 0
    commits: dict[str, str] = {}
    for event, contract in zip(events, expected, strict=True):
        if any(event.get(field) != value for field, value in contract.items()):
            raise _fail("journal_operation_binding_invalid")
        event_type = event.get("event_type")
        expected_fields = {
            "intent": {"event_sequence", "event_type", "operation_id", "source_sha256"},
            "commit": {
                "event_sequence",
                "event_type",
                "operation_id",
                "source_sha256",
                "commit_sha256",
            },
            "publication": {
                "event_sequence",
                "event_type",
                "operation_id",
                "source_sha256",
                "commit_sha256",
                "recovered",
            },
        }[str(event_type)]
        if set(event) != expected_fields:
            raise _fail("journal_event_shape_invalid")
        operation_id = str(event["operation_id"])
        if event_type == "commit":
            commits[operation_id] = _sha(
                event.get("commit_sha256"), "journal_commit_invalid"
            )
        elif event_type == "publication":
            if (
                event.get("commit_sha256") != commits.get(operation_id)
                or not isinstance(event.get("recovered"), bool)
            ):
                raise _fail("journal_publication_binding_invalid")
            recovered += int(event["recovered"])
    return {
        "journal_intent_count": len(operations),
        "journal_commit_count": len(commits),
        "journal_publication_count": len(operations),
        "journal_recovered_publication_count": recovered,
    }, payload_sha256(events)


def build_s5_mstar_post_observation(
    *,
    run_id: str,
    expected_sources: Sequence[Mapping[str, object]],
    attempt_publications: Sequence[Mapping[str, object]],
    journal_events: Sequence[Mapping[str, object]],
    native_observation: Mapping[str, object],
) -> dict[str, object]:
    """Build a sealed three-way publication observation for one successful M*."""

    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise _fail("run_id_invalid")
    sources = _sources(expected_sources)
    publications = _publication_projection(attempt_publications)
    if publications != sources:
        raise _fail("attempt_publication_coverage_invalid")
    try:
        native = verify_s5_native_post_observation(
            native_observation,
            expected_method="M*",
            expected_run_id=run_id,
            expected_namespace=f"pev3-{run_id}",
        )
    except Exception:
        raise _fail("native_observation_invalid") from None
    source_manifest = payload_sha256(sources)
    if (
        native.get("source_manifest_sha256") != source_manifest
        or native.get("durable_publication_manifest_sha256")
        != payload_sha256(publications)
    ):
        raise _fail("native_observation_binding_invalid")
    operations = _operation_manifest(run_id, sources)
    journal, journal_digest = _journal_summary(
        journal_events, operations=operations
    )
    violations = int(native["global_violation_total"])
    public_native = deepcopy(dict(native_observation))
    value: dict[str, object] = {
        "schema_version": SCHEMA,
        "method": "M*",
        "status": (
            "PASS" if violations == 0 else "DIRECT_INVARIANT_VIOLATION_OBSERVED"
        ),
        "run_id_sha256": hashlib.sha256(run_id.encode("utf-8")).hexdigest(),
        "source_manifest_sha256": source_manifest,
        "attempt_publication_manifest_sha256": payload_sha256(publications),
        "operation_manifest_sha256": payload_sha256(operations),
        "journal_events_sha256": journal_digest,
        "native_observation_sha256": native["observation_sha256"],
        "native_observation": public_native,
        "summary": {
            "expected_source_count": 49,
            "attempt_publication_count": len(publications),
            **journal,
            "global_violation_total": violations,
        },
    }
    value["post_observation_sha256"] = payload_sha256(value)
    return verify_s5_mstar_post_observation(value, expected_run_id=run_id)


def verify_s5_mstar_post_observation(
    value: Mapping[str, object], *, expected_run_id: str | None = None
) -> dict[str, object]:
    """Verify the public artifact; optionally rebind deterministic operation IDs."""

    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise _fail("post_observation_invalid")
    artifact = deepcopy(dict(value))
    seal = artifact.pop("post_observation_sha256", None)
    if (
        artifact.get("schema_version") != SCHEMA
        or artifact.get("method") != "M*"
        or artifact.get("status")
        not in {"PASS", "DIRECT_INVARIANT_VIOLATION_OBSERVED"}
        or not isinstance(seal, str)
        or seal != payload_sha256(artifact)
    ):
        raise _fail("post_observation_invalid")
    for field in (
        "run_id_sha256",
        "source_manifest_sha256",
        "attempt_publication_manifest_sha256",
        "operation_manifest_sha256",
        "journal_events_sha256",
        "native_observation_sha256",
    ):
        _sha(artifact.get(field), "post_observation_identity_invalid")
    try:
        native = verify_s5_native_post_observation(
            artifact.get("native_observation", {}),
            **(
                {
                    "expected_method": "M*",
                    "expected_run_id": expected_run_id,
                    "expected_namespace": f"pev3-{expected_run_id}",
                }
                if expected_run_id is not None
                else {}
            ),
        )
    except Exception:
        raise _fail("native_observation_invalid") from None
    sources = [
        {
            "source_sequence": row["source_sequence"],
            "source_sha256": row["source_sha256"],
        }
        for row in native["source_classifications"]
    ]
    summary = artifact.get("summary")
    violations = int(native["global_violation_total"])
    expected_status = (
        "PASS" if violations == 0 else "DIRECT_INVARIANT_VIOLATION_OBSERVED"
    )
    if (
        not isinstance(summary, Mapping)
        or set(summary) != _SUMMARY_FIELDS
        or any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in summary.values()
        )
        or summary.get("expected_source_count") != 49
        or summary.get("attempt_publication_count") != 49
        or summary.get("journal_intent_count") != 49
        or summary.get("journal_commit_count") != 49
        or summary.get("journal_publication_count") != 49
        or int(summary.get("journal_recovered_publication_count", -1)) > 49
        or summary.get("global_violation_total") != violations
        or artifact.get("status") != expected_status
        or artifact.get("source_manifest_sha256") != payload_sha256(sources)
        or artifact.get("attempt_publication_manifest_sha256")
        != payload_sha256(sources)
        or artifact.get("native_observation_sha256")
        != native.get("observation_sha256")
    ):
        raise _fail("post_observation_summary_invalid")
    if expected_run_id is not None:
        if (
            not isinstance(expected_run_id, str)
            or _RUN_ID.fullmatch(expected_run_id) is None
            or artifact.get("run_id_sha256")
            != hashlib.sha256(expected_run_id.encode("utf-8")).hexdigest()
            or artifact.get("operation_manifest_sha256")
            != payload_sha256(_operation_manifest(expected_run_id, sources))
        ):
            raise _fail("post_observation_run_binding_invalid")
    # The generic verifier returns an analysis projection with integer
    # per-source keys.  Preserve the canonical persisted form so this verified
    # artifact remains round-trip verifiable.
    artifact["native_observation"] = deepcopy(dict(value["native_observation"]))
    artifact["post_observation_sha256"] = seal
    return artifact


__all__ = [
    "S5MStarPostObservationError",
    "build_s5_mstar_post_observation",
    "verify_s5_mstar_post_observation",
]
