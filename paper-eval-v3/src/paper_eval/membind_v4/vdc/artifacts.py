"""Durable artifacts and offline row construction for the VDC capture run."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields
from pathlib import Path

from paper_eval.artifacts import atomic_write_json, payload_sha256

from .certificate import (
    DependencyClass,
    FrontierDependencyCertificate,
    classify_early_execution,
)
from .live_composition import VDCObservationBundle
from .oracle import VDCOracleRow


class VDCArtifactError(ValueError):
    """A durable VDC artifact is malformed or cannot support the oracle."""


def _fail(code: str) -> VDCArtifactError:
    return VDCArtifactError(code)


def _int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def read_publication_times(path: Path) -> dict[int, int]:
    """Read publication timestamps from a v3.1 lifecycle ledger with hashes."""

    selected: dict[int, int] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise _fail("lifecycle_ledger_unreadable") from None
    for line in lines:
        try:
            wrapper = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise _fail("lifecycle_event_invalid") from None
        if not isinstance(wrapper, Mapping) or not isinstance(
            wrapper.get("event"), Mapping
        ):
            raise _fail("lifecycle_event_invalid")
        event = dict(wrapper["event"])
        if wrapper.get("event_sha256") != payload_sha256(event):
            raise _fail("lifecycle_event_hash_mismatch")
        if event.get("event_type") != "PUBLICATION_DURABLE":
            continue
        sequence = _int(event.get("source_sequence"), "publication_source_invalid")
        timestamp = _int(event.get("timestamp_ns"), "publication_timestamp_invalid")
        if sequence in selected:
            raise _fail("publication_source_duplicate")
        selected[sequence] = timestamp
    return selected


def _observation_document(value: object) -> dict[str, object]:
    if hasattr(value, "certificate"):
        certificate = value.certificate
        body = {
            field.name: deepcopy(getattr(value, field.name))
            for field in fields(value)
            if field.name != "certificate"
        }
        body["certificate"] = certificate.to_document()
        return body
    try:
        return {
            field.name: deepcopy(getattr(value, field.name))
            for field in fields(value)
        }
    except (TypeError, AttributeError):
        raise _fail("observation_projection_invalid") from None


def bundle_document(bundle: VDCObservationBundle) -> dict[str, object]:
    if not isinstance(bundle, VDCObservationBundle):
        raise _fail("observation_bundle_invalid")
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.vdc-capture-bundle.v1",
        "captures": [
            bundle.captures[key].to_document() for key in sorted(bundle.captures)
        ],
        "prepared": [
            _observation_document(bundle.prepared[key]) for key in sorted(bundle.prepared)
        ],
        "stale_probes": [
            _observation_document(bundle.stale_probes[key])
            for key in sorted(bundle.stale_probes)
        ],
        "exact_reads": [
            _observation_document(bundle.exact_reads[key])
            for key in sorted(bundle.exact_reads)
        ],
        "capture_count": len(bundle.captures),
        "prepared_count": len(bundle.prepared),
        "stale_probe_count": len(bundle.stale_probes),
        "exact_read_count": len(bundle.exact_reads),
    }
    return {**body, "payload_sha256": payload_sha256(body)}


def write_vdc_bundle(path: Path, bundle: VDCObservationBundle) -> dict[str, object]:
    target = Path(path)
    if target.exists():
        raise _fail("bundle_output_exists")
    document = bundle_document(bundle)
    atomic_write_json(target, document)
    return document


def _frontier_certificate(
    bundle: VDCObservationBundle,
    source_sequence: int,
):
    exact = bundle.exact_reads.get(source_sequence)
    capture = bundle.captures.get(source_sequence)
    if exact is None:
        raise _fail("frontier_exact_read_missing")
    if capture is None:
        raise _fail("frontier_capture_missing")
    effect_nodes = capture.effect.get("resolved_nodes")
    if isinstance(effect_nodes, (str, bytes)) or not isinstance(effect_nodes, list):
        raise _fail("frontier_effect_nodes_missing")
    node_write_ids: list[str] = []
    for node in effect_nodes:
        if not isinstance(node, Mapping) or not isinstance(node.get("uuid"), str):
            raise _fail("frontier_effect_node_uuid_missing")
        node_write_ids.append(str(node["uuid"]))
    episode_uuid = capture.episode.get("uuid")
    if not isinstance(episode_uuid, str) or not episode_uuid:
        raise _fail("frontier_episode_uuid_missing")
    return FrontierDependencyCertificate.create(
        source_sequence=source_sequence,
        group_id=capture.group_id,
        semantic_keys=exact.certificate.semantic_keys,
        candidate_ids=exact.certificate.candidate_ids,
        node_write_ids=tuple(sorted(set(node_write_ids))),
        publishes_episode=True,
        effect_scope_complete=True,
        published_episode_uuid=episode_uuid,
    )


def build_vdc_oracle_rows(
    bundle: VDCObservationBundle,
    *,
    publication_times: Mapping[int, int],
    expected_source_sequences: tuple[int, ...] = tuple(range(1, 12)),
) -> list[VDCOracleRow]:
    if not isinstance(bundle, VDCObservationBundle):
        raise _fail("observation_bundle_invalid")
    rows: list[VDCOracleRow] = []
    for source_sequence in expected_source_sequences:
        if not isinstance(source_sequence, int) or source_sequence < 0:
            raise _fail("source_sequence_invalid")
        exact = bundle.exact_reads.get(source_sequence)
        if exact is None:
            raise _fail("exact_read_missing")
        prepared = bundle.prepared.get(source_sequence)
        if prepared is None:
            raise _fail("prepared_observation_missing")
        predecessor = source_sequence - 1
        predecessor_publication_ns = publication_times.get(predecessor)
        if predecessor_publication_ns is None:
            raise _fail("predecessor_publication_missing")
        _int(predecessor_publication_ns, "predecessor_publication_invalid")
        stale = bundle.stale_probes.get(source_sequence)
        dependency_class = DependencyClass.UNKNOWN
        if stale is not None:
            frontier = _frontier_certificate(bundle, predecessor)
            dependency_class = classify_early_execution(
                frontier,
                stale.certificate,
            ).dependency_class
        service_ns = exact.resolve_completed_ns - exact.resolve_started_ns
        if service_ns < 0:
            raise _fail("exact_resolve_timing_invalid")
        rows.append(
            VDCOracleRow(
                source_sequence=source_sequence,
                prepared_durable_ns=prepared.artifact_ready_ns,
                predecessor_publication_ns=predecessor_publication_ns,
                stale_probe_completed_ns=(
                    None if stale is None else stale.probe_completed_ns
                ),
                dependency_class=dependency_class,
                stale_read=None if stale is None else stale.certificate,
                exact_read=exact.certificate,
                exact_node_resolve_service_ns=service_ns,
            )
        )
    return rows


__all__ = [
    "VDCArtifactError",
    "build_vdc_oracle_rows",
    "bundle_document",
    "read_publication_times",
    "write_vdc_bundle",
]
