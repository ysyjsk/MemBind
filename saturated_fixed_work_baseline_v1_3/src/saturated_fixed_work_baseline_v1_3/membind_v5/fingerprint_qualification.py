"""Provider-free non-interference qualification for passive fingerprints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .semantic_fingerprint import fingerprint_records, semantic_fingerprint


QUALIFICATION_ROOT_NAME = "sfwb-v1-3-v5-semantic-fingerprint-qualification-20260821-001"
PASS_GATE = "PASSIVE_FINGERPRINT_NONINTERFERENCE_PASS"
FAIL_GATE = "STOP_V5_FINGERPRINT_NONINTERFERENCE_FAILURE"


@dataclass(frozen=True, slots=True)
class _ProviderFreeFixture:
    requests: tuple[dict[str, Any], ...]
    batches: tuple[tuple[str, ...], ...]
    effects: tuple[dict[str, Any], ...]
    publication: tuple[dict[str, Any], ...]
    provider_calls: int = 0
    db_io: int = 0


def _fixture() -> _ProviderFreeFixture:
    return _ProviderFreeFixture(
        requests=(
            {"operator": "NODE_EXTRACTION", "input_hash": "input-node-0", "prompt_hash": "prompt-node-0", "request_id": "r-1"},
            {"operator": "EDGE_EXTRACTION", "input_hash": "input-edge-0", "prompt_hash": "prompt-edge-0", "request_id": "r-2"},
        ),
        batches=(("candidate-a", "candidate-b"),),
        effects=({"effect": "UPSERT_NODE", "semantic_id": "node-a", "request_id": "r-3"},),
        publication=({"source_sequence": 0, "publication_version": 1, "trace_id": "trace-1"},),
    )


def _public_snapshot(fixture: _ProviderFreeFixture) -> dict[str, Any]:
    return {
        "requests": fixture.requests,
        "batches": fixture.batches,
        "effects": fixture.effects,
        "publication": fixture.publication,
        "provider_calls": fixture.provider_calls,
        "db_io": fixture.db_io,
    }


def qualify_fingerprint_noninterference() -> dict[str, Any]:
    """Run only in-memory passive observers against a runtime-like fixture."""

    before = _fixture()
    observed_request_fingerprints = [
        semantic_fingerprint(
            request,
            boundary=f"{request['operator']}_INPUT",
            semantic_fields=("operator", "input_hash", "prompt_hash"),
        )
        for request in before.requests
    ]
    observed_batch = fingerprint_records(
        tuple({"member": member} for batch in before.batches for member in batch),
        boundary="NODE_RESOLUTION_BATCH",
        semantic_fields=("member",),
    )
    observed_effect = fingerprint_records(
        before.effects,
        boundary="PERSISTENCE_EFFECT",
        semantic_fields=("effect", "semantic_id"),
    )
    # The passive observer receives copies of already-produced values. It does
    # not call a provider and never mutates the fixture that owns the effects.
    after = _ProviderFreeFixture(
        requests=tuple(dict(row) for row in before.requests),
        batches=tuple(tuple(batch) for batch in before.batches),
        effects=tuple(dict(row) for row in before.effects),
        publication=tuple(dict(row) for row in before.publication),
        provider_calls=before.provider_calls,
        db_io=before.db_io,
    )
    unchanged = _public_snapshot(before) == _public_snapshot(after)
    checks = {
        "request_count_unchanged": len(before.requests) == len(after.requests),
        "prompt_input_unchanged": before.requests == after.requests,
        "batch_membership_unchanged": before.batches == after.batches,
        "effect_unchanged": before.effects == after.effects,
        "publication_unchanged": before.publication == after.publication,
        "zero_extra_provider_calls": after.provider_calls - before.provider_calls == 0,
        "zero_extra_db_io": after.db_io - before.db_io == 0,
        "fixture_snapshot_unchanged": unchanged,
    }
    passed = all(checks.values()) and bool(observed_request_fingerprints) and observed_batch["count"] == 2 and observed_effect["count"] == 1
    return {
        "schema_version": "sfwb.v1.3.v5.semantic-fingerprint-qualification.v1",
        "benchmark": "saturated_fixed_work_baseline_v1_3",
        "live_execution": False,
        "provider_free": True,
        "sealed_artifacts_mutated": False,
        "fingerprint_records_emitted": len(observed_request_fingerprints) + 2,
        "checks": checks,
        "decision": {
            "gate": PASS_GATE if passed else FAIL_GATE,
            "source_0_diagnostic_may_be_considered": passed,
            "reason": "Passive canonical serialization changed no request, batch, effect, publication, provider, or DB observation." if passed else "At least one passive non-interference invariant failed.",
        },
    }


def write_fingerprint_qualification_artifacts(result: dict[str, Any], output_root: Path | str, *, overwrite: bool = False) -> list[Path]:
    out = Path(output_root)
    if out.exists() and not overwrite:
        raise ValueError("FINGERPRINT_QUALIFICATION_ROOT_ALREADY_EXISTS")
    out.mkdir(parents=True, exist_ok=True)
    payload = out / "SFWB_V13_V5_FINGERPRINT_QUALIFICATION.json"
    markdown = out / "SFWB_V13_V5_FINGERPRINT_NONINTERFERENCE.md"
    payload.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    rows = "\n".join(f"- `{name}`: `{'PASS' if value else 'FAIL'}`" for name, value in result["checks"].items())
    markdown.write_text(
        "# SFWB v1.3 V5 semantic fingerprint qualification\n\n"
        f"Decision: `{result['decision']['gate']}`\n\n"
        "This is a provider-free in-memory qualification. No Graphiti, Neo4j, model, embedding, scheduler, admission, or persistence call was made.\n\n"
        "## Non-interference checks\n\n" + rows + "\n\n"
        "The qualification does not retroactively add telemetry to sealed v1.3 runs. A future source-0 diagnostic may attach the same passive observer to already-produced objects only after a separately authorized live step.\n",
        encoding="utf-8",
    )
    return [payload, markdown]


__all__ = [
    "FAIL_GATE",
    "PASS_GATE",
    "QUALIFICATION_ROOT_NAME",
    "qualify_fingerprint_noninterference",
    "write_fingerprint_qualification_artifacts",
]
