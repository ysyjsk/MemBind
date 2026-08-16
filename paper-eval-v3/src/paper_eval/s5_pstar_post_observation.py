"""Sanitized bounded namespace observation for both P* terminal branches.

The whole-update scheduler can terminate either after all 49 publications or
after one treatment-induced Native failure. This artifact keeps the complete
source terminal ledger and binds the independently observed Episodic subset
and direct-invariant counts without exposing graph content.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy

from .artifacts import payload_sha256


SCHEMA = "membind.paper-eval-v3.s5-pstar-post-observation.v1"
_RUN = re.compile(r"^s5-p-star-[0-9]{8}-[0-9]{3}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_KINDS = {
    "PUBLISHED",
    "TREATMENT_FAILED",
    "CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE",
}
_FIELDS = {
    "schema_version",
    "method",
    "status",
    "run_id_sha256",
    "source_manifest_sha256",
    "published_manifest_sha256",
    "source_classifications",
    "published_source_sequences",
    "accounting",
    "violation_counts",
    "per_source_violation_counts",
    "global_violation_total",
    "observation_sha256",
}


class S5PStarPostObservationError(ValueError):
    """P* terminal accounting or namespace observation is invalid."""


def _fail(code: str) -> S5PStarPostObservationError:
    return S5PStarPostObservationError(code)


def _sources(rows: object, code: str) -> list[dict[str, object]]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise _fail(code)
    selected: list[dict[str, object]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise _fail(code)
        row = deepcopy(dict(raw))
        if (
            set(row) != {"source_sequence", "source_sha256"}
            or isinstance(row.get("source_sequence"), bool)
            or not isinstance(row.get("source_sequence"), int)
            or not isinstance(row.get("source_sha256"), str)
            or _SHA.fullmatch(str(row["source_sha256"])) is None
        ):
            raise _fail(code)
        selected.append(row)
    return selected


def _nonnegative_counts(value: object, code: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    selected: dict[str, int] = {}
    for key, count in value.items():
        if (
            not isinstance(key, str)
            or not key
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise _fail(code)
        selected[key] = count
    return selected


def build_s5_pstar_post_observation(
    *,
    run_id: str,
    expected_sources: Sequence[Mapping[str, object]],
    source_terminals: Sequence[Mapping[str, object]],
    observed_episodics: Sequence[Mapping[str, object]],
    violation_counts: Mapping[str, int],
    per_source_violation_counts: Mapping[str, int],
) -> dict[str, object]:
    """Build one public observation from independently supplied query results."""

    if not isinstance(run_id, str) or _RUN.fullmatch(run_id) is None:
        raise _fail("run_id_invalid")
    expected = _sources(expected_sources, "expected_sources_invalid")
    if (
        len(expected) != 49
        or [row["source_sequence"] for row in expected] != list(range(49))
        or len({row["source_sha256"] for row in expected}) != 49
    ):
        raise _fail("expected_sources_invalid")
    if (
        isinstance(source_terminals, (str, bytes))
        or not isinstance(source_terminals, Sequence)
        or len(source_terminals) != 49
    ):
        raise _fail("terminal_accounting_invalid")
    terminals: list[dict[str, object]] = []
    for raw in source_terminals:
        if not isinstance(raw, Mapping):
            raise _fail("terminal_accounting_invalid")
        row = deepcopy(dict(raw))
        if set(row) != {
            "source_sequence",
            "source_sha256",
            "terminal_classification",
        }:
            raise _fail("terminal_accounting_invalid")
        index = row.get("source_sequence")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index not in range(49)
            or row.get("source_sha256") != expected[index]["source_sha256"]
            or row.get("terminal_classification") not in _KINDS
        ):
            raise _fail("terminal_accounting_invalid")
        terminals.append(row)
    if Counter(row["source_sequence"] for row in terminals) != Counter(range(49)):
        raise _fail("terminal_accounting_invalid")
    terminals.sort(key=lambda row: int(row["source_sequence"]))

    kinds = Counter(row["terminal_classification"] for row in terminals)
    published = [
        expected[int(row["source_sequence"])]
        for row in terminals
        if row["terminal_classification"] == "PUBLISHED"
    ]
    observed = _sources(observed_episodics, "episodic_observation_invalid")
    if Counter(
        (row["source_sequence"], row["source_sha256"]) for row in observed
    ) != Counter(
        (row["source_sequence"], row["source_sha256"]) for row in published
    ):
        raise _fail("episodic_publication_mismatch")

    failed = kinds["TREATMENT_FAILED"]
    censored = kinds["CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE"]
    if failed not in {0, 1} or (failed == 0) != (censored == 0):
        raise _fail("terminal_classification_invalid")
    aggregate = _nonnegative_counts(violation_counts, "violation_counts_invalid")
    per_source = _nonnegative_counts(
        per_source_violation_counts, "per_source_violation_counts_invalid"
    )
    published_sequences = [int(row["source_sequence"]) for row in published]
    if set(per_source) != {str(index) for index in published_sequences}:
        raise _fail("per_source_violation_coverage_invalid")

    total = sum(aggregate.values())
    status = (
        "TREATMENT_FAILURE_OBSERVED"
        if failed
        else "DIRECT_INVARIANT_VIOLATION_OBSERVED"
        if total
        else "PASS"
    )
    value: dict[str, object] = {
        "schema_version": SCHEMA,
        "method": "P*",
        "status": status,
        "run_id_sha256": payload_sha256(run_id),
        "source_manifest_sha256": payload_sha256(expected),
        "published_manifest_sha256": payload_sha256(published),
        "source_classifications": terminals,
        "published_source_sequences": published_sequences,
        "accounting": {
            "expected": 49,
            "published": kinds["PUBLISHED"],
            "failed": failed,
            "censored": censored,
        },
        "violation_counts": dict(sorted(aggregate.items())),
        "per_source_violation_counts": {
            key: per_source[key] for key in sorted(per_source, key=int)
        },
        "global_violation_total": total,
    }
    value["observation_sha256"] = payload_sha256(value)
    return verify_s5_pstar_post_observation(value)


def verify_s5_pstar_post_observation(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Verify the standalone public P* observation."""

    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise _fail("observation_invalid")
    selected = deepcopy(dict(value))
    seal = selected.pop("observation_sha256", None)
    if (
        not isinstance(seal, str)
        or seal != payload_sha256(selected)
        or selected.get("schema_version") != SCHEMA
        or selected.get("method") != "P*"
        or selected.get("status")
        not in {
            "PASS",
            "DIRECT_INVARIANT_VIOLATION_OBSERVED",
            "TREATMENT_FAILURE_OBSERVED",
        }
        or any(
            not isinstance(selected.get(field), str)
            or _SHA.fullmatch(str(selected[field])) is None
            for field in (
                "run_id_sha256",
                "source_manifest_sha256",
                "published_manifest_sha256",
            )
        )
    ):
        raise _fail("observation_invalid")

    terminals = selected.get("source_classifications")
    if (
        not isinstance(terminals, list)
        or len(terminals) != 49
        or [row.get("source_sequence") for row in terminals if isinstance(row, Mapping)]
        != list(range(49))
    ):
        raise _fail("observation_terminal_accounting_invalid")
    published: list[dict[str, object]] = []
    kinds: Counter[str] = Counter()
    for row in terminals:
        if (
            not isinstance(row, Mapping)
            or set(row)
            != {
                "source_sequence",
                "source_sha256",
                "terminal_classification",
            }
            or not isinstance(row.get("source_sha256"), str)
            or _SHA.fullmatch(str(row["source_sha256"])) is None
            or row.get("terminal_classification") not in _KINDS
        ):
            raise _fail("observation_terminal_accounting_invalid")
        kinds[str(row["terminal_classification"])] += 1
        if row["terminal_classification"] == "PUBLISHED":
            published.append(
                {
                    "source_sequence": row["source_sequence"],
                    "source_sha256": row["source_sha256"],
                }
            )
    source_inventory = [
        {
            "source_sequence": row["source_sequence"],
            "source_sha256": row["source_sha256"],
        }
        for row in terminals
    ]
    published_sequences = [int(row["source_sequence"]) for row in published]
    if (
        selected.get("source_manifest_sha256") != payload_sha256(source_inventory)
        or selected.get("published_manifest_sha256") != payload_sha256(published)
        or selected.get("published_source_sequences") != published_sequences
    ):
        raise _fail("observation_manifest_invalid")

    accounting = selected.get("accounting")
    expected_accounting = {
        "expected": 49,
        "published": kinds["PUBLISHED"],
        "failed": kinds["TREATMENT_FAILED"],
        "censored": kinds["CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE"],
    }
    if accounting != expected_accounting or expected_accounting["failed"] not in {0, 1}:
        raise _fail("observation_accounting_invalid")
    if (expected_accounting["failed"] == 0) != (expected_accounting["censored"] == 0):
        raise _fail("observation_accounting_invalid")

    aggregate = _nonnegative_counts(
        selected.get("violation_counts"), "observation_violation_counts_invalid"
    )
    per_source = _nonnegative_counts(
        selected.get("per_source_violation_counts"),
        "observation_per_source_invalid",
    )
    if set(per_source) != {str(index) for index in published_sequences}:
        raise _fail("observation_per_source_invalid")
    total = sum(aggregate.values())
    expected_status = (
        "TREATMENT_FAILURE_OBSERVED"
        if expected_accounting["failed"]
        else "DIRECT_INVARIANT_VIOLATION_OBSERVED"
        if total
        else "PASS"
    )
    if (
        selected.get("global_violation_total") != total
        or selected.get("status") != expected_status
    ):
        raise _fail("observation_summary_invalid")
    selected["observation_sha256"] = seal
    return selected


__all__ = [
    "S5PStarPostObservationError",
    "build_s5_pstar_post_observation",
    "verify_s5_pstar_post_observation",
]
