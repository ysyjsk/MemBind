"""Independent, source-count-neutral Neo4j observation for one S6 block."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256
from .s6_calibration_contract import verify_s6_cell_identity


SCHEMA = "membind.paper-eval-v3.s6-post-observation.v1"
STAGE = "S6_DEVELOPMENT_ONLY_CONCURRENCY_CALIBRATION"
EPISODIC_OBSERVATION = "EPISODIC"
ENTITY_OBSERVATION = "ENTITY"
RELATES_TO_OBSERVATION = "RELATES_TO"
_OBSERVATIONS = (EPISODIC_OBSERVATION, ENTITY_OBSERVATION, RELATES_TO_OBSERVATION)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_TERMINAL_STATUSES = {"PUBLISHED", "FAILED", "CENSORED"}
_DIRECT_VIOLATIONS = (
    "lost_episodic_count",
    "duplicate_episodic_count",
    "unexpected_episodic_count",
    "episodic_namespace_escape_count",
    "entity_namespace_escape_count",
    "relation_namespace_escape_count",
    "endpoint_escape_count",
    "provenance_dangling_count",
    "provenance_cross_namespace_count",
    "valid_invalid_reversal_count",
)
_COUNT_FIELDS = {
    "expected_source_count",
    "published_source_count",
    "failed_source_count",
    "censored_source_count",
    "episodic_count",
    "lost_episodic_count",
    "duplicate_episodic_count",
    "unexpected_episodic_count",
    "episodic_namespace_escape_count",
    "entity_count",
    "relates_to_count",
    "entity_namespace_escape_count",
    "relation_namespace_escape_count",
    "endpoint_escape_count",
    "provenance_dangling_count",
    "provenance_cross_namespace_count",
    "valid_invalid_reversal_count",
}
_FIELDS = {
    "schema_version",
    "stage",
    "method",
    "status",
    "run_id",
    "cell_index",
    "history_id",
    "configured_concurrency",
    "run_id_sha256",
    "namespace_sha256",
    "execution_identity_sha256",
    "source_manifest_sha256",
    "terminal_manifest_sha256",
    "published_manifest_sha256",
    "observed_episodic_manifest_sha256",
    "source_classifications",
    "observed_episodics",
    "counts",
    "per_source_violation_counts",
    "violation_classifications",
    "global_violation_total",
    "observation_sha256",
}


class S6BlockPostprocessError(ValueError):
    """The independent namespace observation is incomplete or inconsistent."""


def _fail(code: str) -> S6BlockPostprocessError:
    return S6BlockPostprocessError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _count(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _sources(value: object, code: str) -> list[dict[str, object]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    selected: list[dict[str, object]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise _fail(code)
        row = deepcopy(dict(raw))
        if (
            set(row) != {"source_sequence", "source_sha256"}
            or row.get("source_sequence") != index
        ):
            raise _fail(code)
        _sha(row.get("source_sha256"), code)
        selected.append(row)
    if not selected:
        raise _fail(code)
    return selected


def _terminals(
    value: object, expected: Sequence[Mapping[str, object]], method: str
) -> list[dict[str, object]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("terminal_accounting_invalid")
    selected: list[dict[str, object]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise _fail("terminal_accounting_invalid")
        row = deepcopy(dict(raw))
        if (
            set(row) != {"source_sequence", "source_sha256", "status"}
            or index >= len(expected)
            or row.get("source_sequence") != index
            or row.get("source_sha256") != expected[index]["source_sha256"]
            or row.get("status") not in _TERMINAL_STATUSES
        ):
            raise _fail("terminal_accounting_invalid")
        selected.append(row)
    if len(selected) != len(expected):
        raise _fail("terminal_accounting_invalid")
    kinds = Counter(str(row["status"]) for row in selected)
    if method == "M*" and kinds != Counter({"PUBLISHED": len(expected)}):
        raise _fail("mstar_terminal_accounting_invalid")
    if kinds["CENSORED"] and not kinds["FAILED"]:
        raise _fail("terminal_accounting_invalid")
    return selected


def _rows(value: object, code: str) -> list[dict[str, object]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    try:
        return [deepcopy(dict(row)) for row in value]
    except (TypeError, ValueError):
        raise _fail(code) from None


def _records(result: object) -> list[dict[str, object]]:
    rows = getattr(result, "records", None)
    if rows is None and isinstance(result, tuple) and result:
        rows = result[0]
    if rows is None and isinstance(result, list):
        rows = result
    return _rows(rows, "query_result_invalid")


class S6GraphitiPostQueryExecutor:
    """Run the same bounded three-query projection for any frozen S6 history."""

    def __init__(
        self, *, history_id: str, expected_sources: Sequence[Mapping[str, object]]
    ) -> None:
        expected = _sources(expected_sources, "expected_sources_invalid")
        self._source_by_name = {
            f"{history_id}::episode::{int(row['source_sequence']):04d}": row
            for row in expected
        }
        self._unexpected_sequence = len(expected)

    async def __call__(
        self, driver: object, observation: str, namespace: str
    ) -> list[dict[str, object]]:
        execute = getattr(driver, "execute_query", None)
        if not callable(execute):
            raise _fail("query_driver_invalid")
        if observation == EPISODIC_OBSERVATION:
            query = """
            MATCH (episode:Episodic)
            WHERE episode.group_id = $namespace
            RETURN episode.uuid AS record_id, episode.name AS name,
                   episode.group_id AS group_id
            """
        elif observation == ENTITY_OBSERVATION:
            query = """
            MATCH (entity:Entity)
            WHERE entity.group_id = $namespace
               OR EXISTS {
                   MATCH (left:Entity)-[relation:RELATES_TO]->(right:Entity)
                   WHERE (left = entity OR right = entity)
                     AND (relation.group_id = $namespace
                          OR left.group_id = $namespace
                          OR right.group_id = $namespace)
               }
            RETURN entity.uuid AS record_id, entity.group_id AS group_id
            """
        elif observation == RELATES_TO_OBSERVATION:
            query = """
            MATCH (source:Entity)-[relation:RELATES_TO]->(target:Entity)
            WHERE relation.group_id = $namespace
               OR source.group_id = $namespace OR target.group_id = $namespace
            OPTIONAL MATCH (episode:Episodic)
            WHERE episode.uuid IN coalesce(relation.episodes, [])
            WITH source, relation, target,
                 [item IN collect(episode) WHERE item IS NOT NULL |
                    {episode_id: item.uuid, group_id: item.group_id}]
                    AS resolved_provenance
            RETURN relation.uuid AS record_id, relation.group_id AS group_id,
                   source.uuid AS source_entity_id,
                   target.uuid AS target_entity_id,
                   coalesce(relation.episodes, []) AS provenance_episode_ids,
                   resolved_provenance, relation.valid_at AS valid_at,
                   relation.invalid_at AS invalid_at,
                   relation.expired_at AS expired_at
            """
        else:
            raise _fail("observation_type_invalid")
        try:
            rows = _records(
                await execute(
                    query,
                    parameters_={"namespace": namespace},
                    database_="neo4j",
                )
            )
        except S6BlockPostprocessError:
            raise
        except Exception:
            raise _fail("query_execution_failed") from None
        if observation == RELATES_TO_OBSERVATION:
            for row in rows:
                resolved = row.get("resolved_provenance")
                ids = row.get("provenance_episode_ids")
                if not isinstance(resolved, list) or not isinstance(ids, list):
                    raise _fail("query_result_invalid")
                by_id = {
                    item.get("episode_id"): item
                    for item in resolved
                    if isinstance(item, Mapping)
                }
                row["provenance"] = [
                    {
                        "episode_id": episode_id,
                        "group_id": by_id.get(episode_id, {}).get("group_id"),
                        "exists": episode_id in by_id,
                    }
                    for episode_id in ids
                ]
        if observation != EPISODIC_OBSERVATION:
            return rows
        mapped: list[dict[str, object]] = []
        for row in rows:
            source = self._source_by_name.get(row.get("name"))
            mapped.append(
                {
                    **row,
                    **(
                        source
                        if source is not None
                        else {
                            "source_sequence": self._unexpected_sequence,
                            "source_sha256": "0" * 64,
                        }
                    ),
                }
            )
        return mapped


def _time(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    value_type = type(value)
    if value_type.__module__ == "neo4j.time" and value_type.__qualname__ == "DateTime":
        converter = getattr(value, "to_native", None)
        if not callable(converter):
            raise _fail("temporal_value_invalid")
        converted = converter()
        if not isinstance(converted, datetime):
            raise _fail("temporal_value_invalid")
        return converted
    if not isinstance(value, str) or not value:
        raise _fail("temporal_value_invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _fail("temporal_value_invalid") from None


def _coverage(
    *,
    published: Sequence[Mapping[str, object]],
    observed: Sequence[Mapping[str, object]],
) -> tuple[int, int, int, dict[int, int]]:
    expected_counter = Counter(
        (int(row["source_sequence"]), str(row["source_sha256"]))
        for row in published
    )
    observed_counter = Counter(
        (int(row["source_sequence"]), str(row["source_sha256"]))
        for row in observed
    )
    lost = 0
    duplicate = 0
    unexpected = 0
    per_source = {int(row["source_sequence"]): 0 for row in published}
    for key, count in expected_counter.items():
        missing = max(0, count - observed_counter[key])
        extra = max(0, observed_counter[key] - count)
        lost += missing
        duplicate += extra
        per_source[key[0]] += missing + extra
    for key, count in observed_counter.items():
        if key not in expected_counter:
            unexpected += count
    return lost, duplicate, unexpected, per_source


async def observe_s6_post_namespace(
    *,
    driver: object,
    cell: Mapping[str, object],
    execution_identity_sha256: str,
    expected_sources: Sequence[Mapping[str, object]],
    source_terminals: Sequence[Mapping[str, object]],
    query_executor: Callable[[object, str, str], Awaitable[Sequence[Mapping[str, object]]]],
) -> dict[str, object]:
    """Bind expected sources, terminal ledger, and independent graph state."""

    try:
        selected_cell = verify_s6_cell_identity(cell)
    except Exception:
        raise _fail("cell_identity_invalid") from None
    expected = _sources(expected_sources, "expected_sources_invalid")
    terminals = _terminals(
        source_terminals, expected, str(selected_cell["method"])
    )
    if not callable(query_executor):
        raise _fail("query_executor_invalid")
    namespace = str(selected_cell["namespace"])
    observed: dict[str, list[dict[str, object]]] = {}
    for kind in _OBSERVATIONS:
        try:
            observed[kind] = _rows(
                await query_executor(driver, kind, namespace),
                "query_result_invalid",
            )
        except S6BlockPostprocessError:
            raise
        except Exception:
            raise _fail("query_execution_failed") from None

    published = [
        {"source_sequence": row["source_sequence"], "source_sha256": row["source_sha256"]}
        for row in terminals
        if row["status"] == "PUBLISHED"
    ]
    episodes = observed[EPISODIC_OBSERVATION]
    episode_ids: set[object] = set()
    source_by_episode_id: dict[object, int] = {}
    public_observed: list[dict[str, object]] = []
    episodic_namespace_escape_count = 0
    for row in episodes:
        record_id = row.get("record_id")
        sequence = row.get("source_sequence")
        digest = row.get("source_sha256")
        if (
            record_id is None
            or record_id in episode_ids
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
        ):
            raise _fail("episodic_record_identity_invalid")
        _sha(digest, "episodic_source_identity_invalid")
        episode_ids.add(record_id)
        source_by_episode_id[record_id] = sequence
        namespace_match = row.get("group_id") == namespace
        episodic_namespace_escape_count += int(not namespace_match)
        public_observed.append(
            {
                "source_sequence": sequence,
                "source_sha256": digest,
                "namespace_match": namespace_match,
            }
        )
    observed_sources = [
        {"source_sequence": row["source_sequence"], "source_sha256": row["source_sha256"]}
        for row in public_observed
    ]
    lost, duplicate, unexpected, per_source = _coverage(
        published=published, observed=observed_sources
    )
    published_keys = {
        (int(row["source_sequence"]), str(row["source_sha256"])) for row in published
    }
    for row in public_observed:
        key = (int(row["source_sequence"]), str(row["source_sha256"]))
        if not row["namespace_match"] and key in published_keys:
            per_source[key[0]] += 1

    entities = observed[ENTITY_OBSERVATION]
    all_entity_ids: set[object] = set()
    namespace_entity_ids: set[object] = set()
    entity_escape = 0
    for row in entities:
        record_id = row.get("record_id")
        if record_id is None or record_id in all_entity_ids:
            raise _fail("entity_record_identity_invalid")
        all_entity_ids.add(record_id)
        if row.get("group_id") == namespace:
            namespace_entity_ids.add(record_id)
        else:
            entity_escape += 1

    counts: dict[str, int] = {
        "expected_source_count": len(expected),
        "published_source_count": sum(row["status"] == "PUBLISHED" for row in terminals),
        "failed_source_count": sum(row["status"] == "FAILED" for row in terminals),
        "censored_source_count": sum(row["status"] == "CENSORED" for row in terminals),
        "episodic_count": len(episodes),
        "lost_episodic_count": lost,
        "duplicate_episodic_count": duplicate,
        "unexpected_episodic_count": unexpected,
        "episodic_namespace_escape_count": episodic_namespace_escape_count,
        "entity_count": len(entities),
        "relates_to_count": len(observed[RELATES_TO_OBSERVATION]),
        "entity_namespace_escape_count": entity_escape,
        "relation_namespace_escape_count": 0,
        "endpoint_escape_count": 0,
        "provenance_dangling_count": 0,
        "provenance_cross_namespace_count": 0,
        "valid_invalid_reversal_count": 0,
    }
    relation_ids: set[object] = set()
    for relation in observed[RELATES_TO_OBSERVATION]:
        record_id = relation.get("record_id")
        if record_id is None or record_id in relation_ids:
            raise _fail("relation_record_identity_invalid")
        relation_ids.add(record_id)
        provenance = _rows(relation.get("provenance"), "provenance_shape_invalid")
        attributable = {
            source_by_episode_id[item.get("episode_id")]
            for item in provenance
            if item.get("episode_id") in source_by_episode_id
            and source_by_episode_id[item.get("episode_id")] in per_source
        }
        relation_violations = 0
        if relation.get("group_id") != namespace:
            counts["relation_namespace_escape_count"] += 1
            relation_violations += 1
        for endpoint in ("source_entity_id", "target_entity_id"):
            if relation.get(endpoint) not in namespace_entity_ids:
                counts["endpoint_escape_count"] += 1
                relation_violations += 1
        if not provenance:
            counts["provenance_dangling_count"] += 1
            relation_violations += 1
        for item in provenance:
            if item.get("exists") is not True:
                counts["provenance_dangling_count"] += 1
                relation_violations += 1
            elif item.get("group_id") != namespace:
                counts["provenance_cross_namespace_count"] += 1
                relation_violations += 1
        valid_at = _time(relation.get("valid_at"))
        invalid_at = _time(relation.get("invalid_at"))
        if valid_at is not None and invalid_at is not None and invalid_at < valid_at:
            counts["valid_invalid_reversal_count"] += 1
            relation_violations += 1
        for source in attributable:
            per_source[source] += relation_violations

    total = sum(counts[field] for field in _DIRECT_VIOLATIONS)
    value: dict[str, object] = {
        "schema_version": SCHEMA,
        "stage": STAGE,
        "method": selected_cell["method"],
        "status": "PASS" if total == 0 else "INVARIANT_VIOLATIONS_OBSERVED",
        "run_id": selected_cell["run_id"],
        "cell_index": selected_cell["cell_index"],
        "history_id": selected_cell["history_id"],
        "configured_concurrency": selected_cell["configured_concurrency"],
        "run_id_sha256": _identity(str(selected_cell["run_id"])),
        "namespace_sha256": _identity(namespace),
        "execution_identity_sha256": _sha(
            execution_identity_sha256, "execution_identity_invalid"
        ),
        "source_manifest_sha256": payload_sha256(expected),
        "terminal_manifest_sha256": payload_sha256(terminals),
        "published_manifest_sha256": payload_sha256(published),
        "observed_episodic_manifest_sha256": payload_sha256(observed_sources),
        "source_classifications": terminals,
        "observed_episodics": public_observed,
        "counts": counts,
        "per_source_violation_counts": {
            str(source): count for source, count in sorted(per_source.items())
        },
        "violation_classifications": [
            {"classification": field, "count": counts[field]}
            for field in _DIRECT_VIOLATIONS
        ],
        "global_violation_total": total,
    }
    value["observation_sha256"] = payload_sha256(value)
    return verify_s6_post_observation(value)


def verify_s6_post_observation(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise _fail("observation_shape_invalid")
    artifact = deepcopy(dict(value))
    seal = artifact.pop("observation_sha256", None)
    if (
        artifact.get("schema_version") != SCHEMA
        or artifact.get("stage") != STAGE
        or artifact.get("method") not in {"P*", "M*"}
        or artifact.get("status") not in {"PASS", "INVARIANT_VIOLATIONS_OBSERVED"}
        or seal != payload_sha256(artifact)
    ):
        raise _fail("observation_identity_invalid")
    run_id = artifact.get("run_id")
    if not isinstance(run_id, str):
        raise _fail("observation_identity_invalid")
    # Reconstruct the public cell without exposing the namespace in this artifact.
    method_slug = "pstar" if artifact["method"] == "P*" else "mstar"
    expected_run = (
        f"s6-{artifact.get('history_id')}-{method_slug}-"
        f"c{artifact.get('configured_concurrency')}-001"
    )
    try:
        verify_s6_cell_identity(
            {
                "cell_index": artifact.get("cell_index"),
                "history_id": artifact.get("history_id"),
                "data_role": "DEVELOPMENT_EXPOSED",
                "method": artifact.get("method"),
                "configured_concurrency": artifact.get("configured_concurrency"),
                "run_id": run_id,
                "namespace": f"pev3-{run_id}",
                "attempt_ordinal": 1,
                "status": "NOT_STARTED",
            }
        )
    except Exception:
        raise _fail("observation_cell_identity_invalid") from None
    if (
        run_id != expected_run
        or artifact.get("run_id_sha256") != _identity(run_id)
        or artifact.get("cell_index") is None
    ):
        raise _fail("observation_identity_invalid")
    _sha(artifact.get("namespace_sha256"), "namespace_identity_invalid")
    _sha(artifact.get("execution_identity_sha256"), "execution_identity_invalid")

    classifications = artifact.get("source_classifications")
    if isinstance(classifications, (str, bytes)) or not isinstance(
        classifications, Sequence
    ):
        raise _fail("terminal_accounting_invalid")
    expected = [
        {"source_sequence": row.get("source_sequence"), "source_sha256": row.get("source_sha256")}
        for row in classifications
        if isinstance(row, Mapping)
    ]
    expected = _sources(expected, "terminal_accounting_invalid")
    terminals = _terminals(classifications, expected, str(artifact["method"]))
    published = [
        {"source_sequence": row["source_sequence"], "source_sha256": row["source_sha256"]}
        for row in terminals
        if row["status"] == "PUBLISHED"
    ]
    observed_raw = artifact.get("observed_episodics")
    if isinstance(observed_raw, (str, bytes)) or not isinstance(observed_raw, Sequence):
        raise _fail("observed_episodics_invalid")
    observed_public: list[dict[str, object]] = []
    observed_sources: list[dict[str, object]] = []
    for row in observed_raw:
        if (
            not isinstance(row, Mapping)
            or set(row) != {"source_sequence", "source_sha256", "namespace_match"}
            or not isinstance(row.get("namespace_match"), bool)
            or isinstance(row.get("source_sequence"), bool)
            or not isinstance(row.get("source_sequence"), int)
        ):
            raise _fail("observed_episodics_invalid")
        _sha(row.get("source_sha256"), "observed_episodics_invalid")
        observed_public.append(deepcopy(dict(row)))
        observed_sources.append(
            {"source_sequence": row["source_sequence"], "source_sha256": row["source_sha256"]}
        )
    if (
        artifact.get("source_manifest_sha256") != payload_sha256(expected)
        or artifact.get("terminal_manifest_sha256") != payload_sha256(terminals)
        or artifact.get("published_manifest_sha256") != payload_sha256(published)
        or artifact.get("observed_episodic_manifest_sha256")
        != payload_sha256(observed_sources)
    ):
        raise _fail("observation_manifest_invalid")
    for field in (
        "source_manifest_sha256",
        "terminal_manifest_sha256",
        "published_manifest_sha256",
        "observed_episodic_manifest_sha256",
    ):
        _sha(artifact.get(field), "observation_manifest_invalid")

    counts_raw = artifact.get("counts")
    if not isinstance(counts_raw, Mapping) or set(counts_raw) != _COUNT_FIELDS:
        raise _fail("observation_counts_invalid")
    counts = {key: _count(counts_raw.get(key), "observation_counts_invalid") for key in _COUNT_FIELDS}
    kinds = Counter(str(row["status"]) for row in terminals)
    lost, duplicate, unexpected, coverage_per_source = _coverage(
        published=published, observed=observed_sources
    )
    expected_count_values = {
        "expected_source_count": len(expected),
        "published_source_count": kinds["PUBLISHED"],
        "failed_source_count": kinds["FAILED"],
        "censored_source_count": kinds["CENSORED"],
        "episodic_count": len(observed_sources),
        "lost_episodic_count": lost,
        "duplicate_episodic_count": duplicate,
        "unexpected_episodic_count": unexpected,
        "episodic_namespace_escape_count": sum(
            row["namespace_match"] is False for row in observed_public
        ),
    }
    if any(counts[key] != expected_value for key, expected_value in expected_count_values.items()):
        raise _fail("observation_counts_invalid")
    per_source_raw = artifact.get("per_source_violation_counts")
    if not isinstance(per_source_raw, Mapping) or set(per_source_raw) != {
        str(row["source_sequence"]) for row in published
    }:
        raise _fail("per_source_violation_counts_invalid")
    per_source = {
        str(key): _count(value, "per_source_violation_counts_invalid")
        for key, value in per_source_raw.items()
    }
    if any(
        per_source[str(source)] < minimum
        for source, minimum in coverage_per_source.items()
    ):
        raise _fail("per_source_violation_counts_invalid")
    violations = artifact.get("violation_classifications")
    expected_violations = [
        {"classification": field, "count": counts[field]}
        for field in _DIRECT_VIOLATIONS
    ]
    total = sum(counts[field] for field in _DIRECT_VIOLATIONS)
    if (
        violations != expected_violations
        or artifact.get("global_violation_total") != total
        or artifact.get("status")
        != ("PASS" if total == 0 else "INVARIANT_VIOLATIONS_OBSERVED")
    ):
        raise _fail("observation_violation_summary_invalid")
    artifact["counts"] = counts
    artifact["per_source_violation_counts"] = per_source
    artifact["observation_sha256"] = seal
    return artifact


def verify_s6_post_observation_artifact(
    value: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise _fail("post_observation_envelope_shape_invalid")
    artifact = deepcopy(dict(value))
    payload = verify_s6_post_observation(
        artifact.get("payload") if isinstance(artifact.get("payload"), Mapping) else {}
    )
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or _GIT_COMMIT.fullmatch(str(artifact.get("git_commit", ""))) is None
        or artifact.get("run_id") != f"{payload['run_id']}-post-observation"
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise _fail("post_observation_envelope_invalid")
    artifact["payload"] = payload
    return artifact


def _write_exclusive(path: Path, value: Mapping[str, object]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written == 0:
                raise OSError("short write while sealing S6 post observation")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def finalize_s6_post_observation(
    *, output_path: Path, payload: Mapping[str, object], git_commit: str
) -> dict[str, object]:
    selected = verify_s6_post_observation(payload)
    artifact = verify_s6_post_observation_artifact(
        {
            "protocol_version": PROTOCOL_VERSION,
            "git_commit": str(git_commit),
            "run_id": f"{selected['run_id']}-post-observation",
            "status": "finalized",
            "payload": selected,
            "payload_sha256": payload_sha256(selected),
        }
    )
    _write_exclusive(Path(output_path), artifact)
    return artifact


__all__ = [
    "ENTITY_OBSERVATION",
    "EPISODIC_OBSERVATION",
    "RELATES_TO_OBSERVATION",
    "S6BlockPostprocessError",
    "S6GraphitiPostQueryExecutor",
    "finalize_s6_post_observation",
    "observe_s6_post_namespace",
    "verify_s6_post_observation",
    "verify_s6_post_observation_artifact",
]
