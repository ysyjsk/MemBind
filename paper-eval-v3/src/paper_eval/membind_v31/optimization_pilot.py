"""Pure contract layer for an isolated MemBind v3.1 W=4 pilot.

This module deliberately does not open a service, create a directory, or
modify the frozen v3.1 plan.  It derives one twelve-source diagnostic
contract from the verified formal plan and provides sealed manifest,
checkpoint, and result records for a future pilot executor.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.apc_aligned_baseline import (
    APC_BASELINE_HISTORIES,
    build_apc_aligned_baseline_plan,
)
from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan


PILOT_SCHEMA = "membind.paper-eval-v3.membind-v31-w4-pilot-contract.v1"
MANIFEST_SCHEMA = "membind.paper-eval-v3.membind-v31-w4-pilot-manifest.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.membind-v31-w4-pilot-checkpoint.v1"
RESULT_SCHEMA = "membind.paper-eval-v3.membind-v31-w4-pilot-result.v1"
PILOT_SOURCE_COUNT = 12
PILOT_METHOD = "MemBind"
PILOT_HISTORY = "07741c45"
COMPILE_WORKERS = 2
LOOKAHEAD = 4
BIND_WORKERS = 1
GLOBAL_LLM_ADMISSION_K = 2
PREFIX_MATCH_UNIT = 16
DECODE_CONTEXT_PARALLEL_SIZE = 1
POLICY = "FRONTIER_FIRST_CACHE_AFFINITY"
ARTIFACT_STATUS = "DIAGNOSTIC_ONLY_NON_MERGEABLE"
MERGE_AUTHORITY = "NONE_NON_MERGEABLE_OPTIMIZATION_PILOT"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^membind-v31-opt-w4-[a-z0-9][a-z0-9-]{2,63}$")
_ATTEMPT_ID = re.compile(
    r"^membind-v31-opt-w4-[a-z0-9][a-z0-9-]{2,63}-attempt-[0-9]{3}$"
)
_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_STATES = (
    "NEW",
    "ARRIVAL",
    "COMPILE_STARTED",
    "PREPARED_DURABLE",
    "BIND_STARTED",
    "COMMIT_RETURNED",
    "PUBLICATION_DURABLE",
    "TERMINAL_FAILURE",
)


class OptimizationPilotError(ValueError):
    """A W=4 pilot identity or sealed diagnostic artifact is invalid."""


def _fail(code: str) -> OptimizationPilotError:
    return OptimizationPilotError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _seal(body: Mapping[str, object], field: str = "payload_sha256") -> dict[str, Any]:
    result = deepcopy(dict(body))
    result[field] = payload_sha256(result)
    return result


def _sealed(value: Mapping[str, object], *, schema: str, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != schema:
        raise _fail("pilot artifact schema invalid")
    selected = deepcopy(dict(value))
    stored = _sha(selected.get(field), "pilot artifact hash invalid")
    body = {key: child for key, child in selected.items() if key != field}
    if payload_sha256(body) != stored:
        raise _fail("pilot artifact hash mismatch")
    if selected.get("artifact_status") != ARTIFACT_STATUS:
        raise _fail("pilot artifact status invalid")
    if selected.get("merge_authority") != MERGE_AUTHORITY:
        raise _fail("pilot merge authority invalid")
    if selected.get("formal_main_table_eligible") is not False:
        raise _fail("pilot merge eligibility invalid")
    return selected


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def derive_w4_pilot_namespace(pilot_run_id: str) -> str:
    """Derive the only namespace accepted for a pilot run identity."""

    if not isinstance(pilot_run_id, str) or _RUN_ID.fullmatch(pilot_run_id) is None:
        raise _fail("pilot run id invalid")
    suffix = pilot_run_id.removeprefix("membind-v31-opt-")
    namespace = f"pev3-opt-membind-v31-{suffix}-membind-{PILOT_HISTORY}"
    if _NAMESPACE.fullmatch(namespace) is None:
        raise _fail("pilot namespace identity invalid")
    return namespace


def derive_w4_pilot_cache_salt(
    *,
    pilot_run_id: str,
    namespace: str,
    parent_formal_plan_payload_sha256: str,
) -> str:
    """Derive a content-addressed cache salt without exposing credentials."""

    expected_namespace = derive_w4_pilot_namespace(pilot_run_id)
    if namespace != expected_namespace:
        raise _fail("pilot namespace identity invalid")
    parent = _sha(parent_formal_plan_payload_sha256, "parent formal plan hash invalid")
    return payload_sha256(
        {
            "schema_version": "membind.paper-eval-v3.membind-v31-w4-pilot-cache-salt.v1",
            "purpose": "W4_BOUNDED_NON_MERGEABLE_PILOT",
            "pilot_run_id": pilot_run_id,
            "parent_formal_plan_payload_sha256": parent,
            "method": PILOT_METHOD,
            "history_id": PILOT_HISTORY,
            "source_sequences": list(range(PILOT_SOURCE_COUNT)),
            "compile_workers": COMPILE_WORKERS,
            "lookahead": LOOKAHEAD,
            "bind_workers": BIND_WORKERS,
            "global_llm_admission_k": GLOBAL_LLM_ADMISSION_K,
            "namespace": namespace,
        }
    )


def _formal(value: Mapping[str, object]) -> dict[str, Any]:
    try:
        return verify_membind_v31_method_plan(value)
    except (TypeError, ValueError):
        raise _fail("parent formal plan invalid") from None


def _prefix_baseline(formal: Mapping[str, object]) -> dict[str, Any]:
    source_inventory = formal.get("history_source_sha256s")
    if not isinstance(source_inventory, Mapping):
        raise _fail("pilot source lineage invalid")
    selected = {history: list(source_inventory.get(history, ())) for history in APC_BASELINE_HISTORIES}
    representative = selected.get(PILOT_HISTORY)
    if representative is None or len(representative) < PILOT_SOURCE_COUNT:
        raise _fail("pilot source prefix unavailable")
    selected[PILOT_HISTORY] = representative[:PILOT_SOURCE_COUNT]
    try:
        return build_apc_aligned_baseline_plan(
            run_id=str(formal["baseline_run_id"]),
            history_source_sha256s=selected,
            interarrival_ns=int(formal["interarrival_ns"]),
            execution_envelope_sha256=str(formal["shared_execution_envelope_sha256"]),
            service_reference_ns=int(formal["service_reference_ns"]),
            normalized_offered_load=float(formal["normalized_offered_load"]),
        )
    except (KeyError, TypeError, ValueError):
        raise _fail("pilot source lineage invalid") from None


def _contract_body(
    *,
    formal: Mapping[str, object],
    pilot_run_id: str,
    attempt_id: str,
    namespace: str,
    cache_salt_sha256: str,
    output_root: Path,
) -> dict[str, object]:
    prefix = _prefix_baseline(formal)
    sources = formal["history_source_sha256s"][PILOT_HISTORY][:PILOT_SOURCE_COUNT]
    arrival = formal["arrival_traces"][PILOT_HISTORY]
    offsets = list(arrival["arrival_offsets_ns"][:PILOT_SOURCE_COUNT])
    expected_cache_salt = derive_w4_pilot_cache_salt(
        pilot_run_id=pilot_run_id,
        namespace=namespace,
        parent_formal_plan_payload_sha256=str(formal["payload_sha256"]),
    )
    if cache_salt_sha256 != expected_cache_salt:
        raise _fail("pilot cache salt identity invalid")
    return {
        "schema_version": PILOT_SCHEMA,
        "status": "AUTHORIZED",
        "artifact_status": ARTIFACT_STATUS,
        "merge_authority": MERGE_AUTHORITY,
        "formal_main_table_eligible": False,
        "authorization_scope": "OPTIMIZATION_PILOT_ONLY",
        "heldout_data_accessed": False,
        "pilot_run_id": pilot_run_id,
        "attempt_id": attempt_id,
        "parent_formal_plan_payload_sha256": formal["payload_sha256"],
        "parent_methodology_sha256": formal["methodology_sha256"],
        "parent_workplan_sha256": formal["workplan_sha256"],
        "baseline_run_id": formal["baseline_run_id"],
        "method": PILOT_METHOD,
        "policy": POLICY,
        "history_id": PILOT_HISTORY,
        "source_sequences": list(range(PILOT_SOURCE_COUNT)),
        "source_count": PILOT_SOURCE_COUNT,
        "source_sha256s": list(sources),
        "source_manifest_sha256": prefix["source_manifest_sha256"],
        "arrival_trace_sha256": prefix["arrival_trace_sha256"],
        "history_arrival_trace_sha256": prefix["arrival_traces"][PILOT_HISTORY][
            "history_arrival_trace_sha256"
        ],
        "arrival_offsets_ns": offsets,
        "interarrival_ns": prefix["interarrival_ns"],
        "shared_execution_envelope_sha256": formal["shared_execution_envelope_sha256"],
        "compile_workers": COMPILE_WORKERS,
        "lookahead": LOOKAHEAD,
        "bind_workers": BIND_WORKERS,
        "global_llm_admission_k": GLOBAL_LLM_ADMISSION_K,
        "prefix_match_unit": PREFIX_MATCH_UNIT,
        "decode_context_parallel_size": DECODE_CONTEXT_PARALLEL_SIZE,
        "namespace": namespace,
        "cache_salt_sha256": cache_salt_sha256,
        "output_root": str(Path(output_root)),
    }


def build_w4_pilot_contract(
    *,
    verified_formal_plan: Mapping[str, object],
    pilot_run_id: str,
    attempt_id: str,
    namespace: str,
    cache_salt_sha256: str,
    output_root: Path,
    compile_workers: int,
    lookahead: int,
    bind_workers: int,
    global_llm_admission_k: int,
    reserved_namespaces: Sequence[str] = (),
    reserved_cache_salts: Sequence[str] = (),
) -> dict[str, Any]:
    """Authorize a fresh W=4 pilot without touching the filesystem."""

    formal = _formal(verified_formal_plan)
    if (
        not isinstance(pilot_run_id, str)
        or _RUN_ID.fullmatch(pilot_run_id) is None
        or not isinstance(attempt_id, str)
        or _ATTEMPT_ID.fullmatch(attempt_id) is None
        or attempt_id != f"{pilot_run_id}-attempt-001"
    ):
        raise _fail("pilot attempt identity invalid")
    if (compile_workers, lookahead, bind_workers, global_llm_admission_k) != (
        COMPILE_WORKERS,
        LOOKAHEAD,
        BIND_WORKERS,
        GLOBAL_LLM_ADMISSION_K,
    ):
        raise _fail("pilot knob identity invalid")
    expected_namespace = derive_w4_pilot_namespace(pilot_run_id)
    if namespace != expected_namespace:
        raise _fail("pilot namespace identity invalid")
    expected_cache_salt = derive_w4_pilot_cache_salt(
        pilot_run_id=pilot_run_id,
        namespace=namespace,
        parent_formal_plan_payload_sha256=str(formal["payload_sha256"]),
    )
    if cache_salt_sha256 != expected_cache_salt:
        raise _fail("pilot cache salt identity invalid")
    formal_namespaces = {str(row["namespace"]) for row in formal["blocks"]}
    formal_salts = {str(row["cache_salt_sha256"]) for row in formal["blocks"]}
    if namespace in formal_namespaces or namespace in set(reserved_namespaces):
        raise _fail("pilot namespace reused")
    if cache_salt_sha256 in formal_salts or cache_salt_sha256 in set(reserved_cache_salts):
        raise _fail("pilot cache salt reused")
    target = Path(output_root)
    if target.exists():
        raise _fail("pilot output root not fresh")
    return _seal(
        _contract_body(
            formal=formal,
            pilot_run_id=pilot_run_id,
            attempt_id=attempt_id,
            namespace=namespace,
            cache_salt_sha256=cache_salt_sha256,
            output_root=target,
        )
    )


def verify_w4_pilot_contract(
    value: Mapping[str, object], *, verified_formal_plan: Mapping[str, object]
) -> dict[str, Any]:
    """Verify a sealed pilot identity without requiring its output root to be absent."""

    contract = _sealed(value, schema=PILOT_SCHEMA, field="payload_sha256")
    formal = _formal(verified_formal_plan)
    try:
        expected = _contract_body(
            formal=formal,
            pilot_run_id=str(contract["pilot_run_id"]),
            attempt_id=str(contract["attempt_id"]),
            namespace=str(contract["namespace"]),
            cache_salt_sha256=str(contract["cache_salt_sha256"]),
            output_root=Path(str(contract["output_root"])),
        )
    except OptimizationPilotError:
        raise _fail("pilot contract identity drift") from None
    if contract != _seal(expected):
        raise _fail("pilot contract identity drift")
    return contract


def build_w4_pilot_manifest(
    contract: Mapping[str, object],
    *,
    verified_formal_plan: Mapping[str, object],
    execution_identity_sha256: str,
    state_cut_certification_sha256: str,
    implementation_sha256: str,
) -> dict[str, Any]:
    """Seal an execution manifest bound to the isolated pilot contract."""

    selected = verify_w4_pilot_contract(contract, verified_formal_plan=verified_formal_plan)
    body = {
        "schema_version": MANIFEST_SCHEMA,
        "artifact_status": ARTIFACT_STATUS,
        "merge_authority": MERGE_AUTHORITY,
        "formal_main_table_eligible": False,
        "pilot_contract_payload_sha256": selected["payload_sha256"],
        "pilot_run_id": selected["pilot_run_id"],
        "attempt_id": selected["attempt_id"],
        "method": selected["method"],
        "policy": selected["policy"],
        "history_id": selected["history_id"],
        "source_count": selected["source_count"],
        "source_sequences": selected["source_sequences"],
        "source_sha256s": selected["source_sha256s"],
        "source_manifest_sha256": selected["source_manifest_sha256"],
        "arrival_trace_sha256": selected["arrival_trace_sha256"],
        "history_arrival_trace_sha256": selected["history_arrival_trace_sha256"],
        "shared_execution_envelope_sha256": selected["shared_execution_envelope_sha256"],
        "namespace": selected["namespace"],
        "cache_salt_sha256": selected["cache_salt_sha256"],
        "compile_workers": selected["compile_workers"],
        "lookahead": selected["lookahead"],
        "bind_workers": selected["bind_workers"],
        "global_llm_admission_k": selected["global_llm_admission_k"],
        "execution_identity_sha256": _sha(execution_identity_sha256, "pilot execution identity invalid"),
        "state_cut_certification_sha256": _sha(
            state_cut_certification_sha256, "pilot certification identity invalid"
        ),
        "implementation_sha256": _sha(implementation_sha256, "pilot implementation identity invalid"),
    }
    return _seal(body, field="manifest_sha256")


def build_w4_pilot_checkpoint(
    manifest: Mapping[str, object], *, source_states: Sequence[str], event_count: int
) -> dict[str, Any]:
    """Build a content-safe checkpoint; failures remain permanently non-reusable."""

    selected = _sealed(manifest, schema=MANIFEST_SCHEMA, field="manifest_sha256")
    if (
        isinstance(source_states, (str, bytes))
        or not isinstance(source_states, Sequence)
        or len(source_states) != PILOT_SOURCE_COUNT
        or any(state not in _STATES for state in source_states)
    ):
        raise _fail("pilot checkpoint state inventory invalid")
    events = _nonnegative_int(event_count, "pilot checkpoint event count invalid")
    states = list(source_states)
    prefix = -1
    for index, state in enumerate(states):
        if state != "PUBLICATION_DURABLE":
            break
        prefix = index
    failed = "TERMINAL_FAILURE" in states
    complete = all(state == "PUBLICATION_DURABLE" for state in states)
    terminal_status = "COMPLETED" if complete else "FAILED_NON_REUSABLE" if failed else "PLANNED" if not events else "RUNNING"
    body = {
        "schema_version": CHECKPOINT_SCHEMA,
        "artifact_status": ARTIFACT_STATUS,
        "merge_authority": MERGE_AUTHORITY,
        "formal_main_table_eligible": False,
        "manifest_sha256": selected["manifest_sha256"],
        "pilot_run_id": selected["pilot_run_id"],
        "attempt_id": selected["attempt_id"],
        "namespace": selected["namespace"],
        "source_count": PILOT_SOURCE_COUNT,
        "event_count": events,
        "source_states": states,
        "completed_source_prefix": prefix,
        "complete_coverage": complete,
        "terminal_status": terminal_status,
        "resume_status": "NOT_NEEDED_COMPLETE" if complete else "NON_REUSABLE" if failed else "PRE_COMMIT_INCOMPLETE",
    }
    return _seal(body, field="checkpoint_sha256")


def build_w4_pilot_result(
    manifest: Mapping[str, object],
    *,
    checkpoint: Mapping[str, object],
    publication_source_sequences: Sequence[int],
    direct_violation_count: int,
    observed_max_inflight: int,
    p95_freshness_ns: int,
    makespan_ns: int,
) -> dict[str, Any]:
    """Seal only a complete, correctness-safe PASS result."""

    selected_manifest = _sealed(manifest, schema=MANIFEST_SCHEMA, field="manifest_sha256")
    selected_checkpoint = _sealed(checkpoint, schema=CHECKPOINT_SCHEMA, field="checkpoint_sha256")
    if (
        selected_checkpoint.get("manifest_sha256") != selected_manifest["manifest_sha256"]
        or selected_checkpoint.get("terminal_status") != "COMPLETED"
        or selected_checkpoint.get("complete_coverage") is not True
        or list(publication_source_sequences) != list(range(PILOT_SOURCE_COUNT))
        or direct_violation_count != 0
        or isinstance(observed_max_inflight, bool)
        or not isinstance(observed_max_inflight, int)
        or not 0 <= observed_max_inflight <= GLOBAL_LLM_ADMISSION_K
        or not isinstance(p95_freshness_ns, int)
        or p95_freshness_ns <= 0
        or not isinstance(makespan_ns, int)
        or makespan_ns <= 0
    ):
        raise _fail("pilot result invalid")
    body = {
        "schema_version": RESULT_SCHEMA,
        "status": "PASS",
        "artifact_status": ARTIFACT_STATUS,
        "merge_authority": MERGE_AUTHORITY,
        "formal_main_table_eligible": False,
        "pilot_run_id": selected_manifest["pilot_run_id"],
        "attempt_id": selected_manifest["attempt_id"],
        "method": selected_manifest["method"],
        "history_id": selected_manifest["history_id"],
        "namespace": selected_manifest["namespace"],
        "source_count": PILOT_SOURCE_COUNT,
        "source_sequences": list(range(PILOT_SOURCE_COUNT)),
        "manifest_sha256": selected_manifest["manifest_sha256"],
        "checkpoint_sha256": selected_checkpoint["checkpoint_sha256"],
        "compile_workers": selected_manifest["compile_workers"],
        "lookahead": selected_manifest["lookahead"],
        "bind_workers": selected_manifest["bind_workers"],
        "global_llm_admission_k": selected_manifest["global_llm_admission_k"],
        "direct_violation_count": direct_violation_count,
        "observed_max_inflight": observed_max_inflight,
        "publication_source_sequences": list(publication_source_sequences),
        "performance": {
            "p95_freshness_ns": p95_freshness_ns,
            "makespan_ns": makespan_ns,
        },
    }
    return _seal(body)


__all__ = [
    "ARTIFACT_STATUS",
    "BIND_WORKERS",
    "COMPILE_WORKERS",
    "DECODE_CONTEXT_PARALLEL_SIZE",
    "GLOBAL_LLM_ADMISSION_K",
    "LOOKAHEAD",
    "MERGE_AUTHORITY",
    "OptimizationPilotError",
    "PILOT_HISTORY",
    "PILOT_SCHEMA",
    "PILOT_SOURCE_COUNT",
    "build_w4_pilot_checkpoint",
    "build_w4_pilot_contract",
    "build_w4_pilot_manifest",
    "build_w4_pilot_result",
    "derive_w4_pilot_cache_salt",
    "derive_w4_pilot_namespace",
    "verify_w4_pilot_contract",
]
