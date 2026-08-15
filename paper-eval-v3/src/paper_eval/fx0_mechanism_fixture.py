"""Offline FX0 production-path mechanism fixture contract and harness.

FX0 is a correctness harness, not a second MemBind implementation.  The
caller must inject the production mechanism adapter; this module controls only
declared nondeterminism and compares canonical logical outcomes exactly.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import canonical_bytes, finalize_envelope, payload_sha256


SCHEMA = "membind.paper-eval-v3.fx0-mechanism-fixture.v1"
LANE = "FX0_DETERMINISTIC_MECHANISM_FIXTURE"
FIXTURE_COUNT_POLICY = "TRANSITION_COVERAGE_NOT_FIXED_COUNT"

FX0_REQUIRED_TRANSITIONS = (
    "ENTITY_ALIAS_CANONICAL_MERGE",
    "COMPATIBLE_DUPLICATE_UUID_COALESCING",
    "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED",
    "RELATION_RESOLUTION",
    "TEMPORAL_INVALIDATION_UPDATE",
    "PREPARE_TO_BIND_STATE_CHANGE",
    "SOURCE_ORDERED_PUBLICATION",
    "RETRY_IDEMPOTENCE",
    "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
)
FX0_REQUIRED_FAILURE_MODES = (
    "LOST_PUBLICATION",
    "DUPLICATE_PUBLICATION",
    "PARTIAL_PUBLICATION",
)
CONTROLLED_PROVIDER_NAMES = (
    "LLM_RESPONSES",
    "EMBEDDINGS",
    "LOGICAL_TIME",
    "INITIAL_GRAPH_STATE",
    "CANDIDATE_SETS",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_STATUS = {"PASS", "FAIL_CLOSED"}
_ALLOWED_FAILURES = {
    "CONFLICTING_DUPLICATE_UUID",
    *FX0_REQUIRED_FAILURE_MODES,
}
_AUTHORITY = {
    "fx0_offline_design_authorized": True,
    "fx0_live_execution_authorized": False,
    "s5_offline_design_authorized": True,
    "s5_live_execution_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "model_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "current_stage_pointer_update_authorized": False,
}
_FORBIDDEN_ARTIFACT_KEYS = {
    "answer",
    "api_key",
    "content",
    "messages",
    "password",
    "prompt",
    "question",
    "raw_output",
    "raw_response",
    "secret",
    "source",
}


class Fx0FixtureError(ValueError):
    """FX0 input, production-path execution, or exact parity is invalid."""


def _copy_mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Fx0FixtureError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _copy_sequence_of_mappings(
    value: object, *, label: str
) -> tuple[dict[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise Fx0FixtureError(f"{label} must be a sequence")
    result = []
    for item in value:
        result.append(_copy_mapping(item, label=f"{label} item"))
    return tuple(result)


def _sha(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise Fx0FixtureError(f"{field_name} must be a lowercase SHA256")
    return value


def _canonical_equal(left: object, right: object) -> bool:
    try:
        return canonical_bytes(left) == canonical_bytes(right)
    except (TypeError, ValueError):
        raise Fx0FixtureError("FX0 values must be canonical-JSON compatible") from None


@dataclass(frozen=True)
class ControlledNondeterminism:
    """The complete allowlist of providers FX0 may replace."""

    llm_responses: Mapping[str, Any] = field(default_factory=dict)
    embeddings: Mapping[str, Any] = field(default_factory=dict)
    logical_times: Sequence[str] = field(default_factory=tuple)
    initial_state: Mapping[str, Any] = field(default_factory=dict)
    candidate_sets: Sequence[Mapping[str, Any]] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "llm_responses",
            _copy_mapping(self.llm_responses, label="LLM responses"),
        )
        object.__setattr__(
            self,
            "embeddings",
            _copy_mapping(self.embeddings, label="embeddings"),
        )
        if isinstance(self.logical_times, (str, bytes)) or not isinstance(
            self.logical_times, Sequence
        ):
            raise Fx0FixtureError("logical times must be a sequence")
        times = tuple(self.logical_times)
        if any(not isinstance(item, str) or not item for item in times):
            raise Fx0FixtureError("logical times must contain nonempty strings")
        object.__setattr__(self, "logical_times", times)
        object.__setattr__(
            self,
            "initial_state",
            _copy_mapping(self.initial_state, label="initial graph state"),
        )
        object.__setattr__(
            self,
            "candidate_sets",
            _copy_sequence_of_mappings(
                self.candidate_sets, label="candidate sets"
            ),
        )

    def hash_projection(self) -> dict[str, Any]:
        """Return hash-only provider evidence suitable for a public artifact."""

        return {
            "llm_responses_sha256": payload_sha256(self.llm_responses),
            "embeddings_sha256": payload_sha256(self.embeddings),
            "logical_times_sha256": payload_sha256(self.logical_times),
            "initial_graph_state_sha256": payload_sha256(self.initial_state),
            "candidate_sets_sha256": payload_sha256(self.candidate_sets),
        }


@dataclass(frozen=True)
class Fx0ExecutionCase:
    """Oracle-free input exposed to the production-path adapter."""

    case_id: str
    source_sequence: int
    source: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise Fx0FixtureError("execution case id must be nonempty")
        if (
            isinstance(self.source_sequence, bool)
            or not isinstance(self.source_sequence, int)
            or self.source_sequence < 0
        ):
            raise Fx0FixtureError("execution source sequence must be nonnegative")
        object.__setattr__(
            self,
            "source",
            _copy_mapping(self.source, label="execution source"),
        )


@dataclass(frozen=True)
class Fx0FixtureCase:
    """One transition case plus its exact semantic oracle."""

    case_id: str
    transition: str
    source_sequence: int
    source: Mapping[str, Any]
    providers: ControlledNondeterminism
    expected_status: str
    expected_error_code: str | None
    expected_canonical_logical_state: Mapping[str, Any]
    expected_publication_history: Sequence[Mapping[str, Any]]

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise Fx0FixtureError("fixture case id must be nonempty")
        if self.transition not in FX0_REQUIRED_TRANSITIONS:
            raise Fx0FixtureError("fixture transition is outside the frozen inventory")
        if not isinstance(self.source_sequence, int) or self.source_sequence < 0:
            raise Fx0FixtureError("fixture source sequence must be nonnegative")
        if not isinstance(self.providers, ControlledNondeterminism):
            raise Fx0FixtureError("fixture providers are not controlled")
        if self.expected_status not in _ALLOWED_STATUS:
            raise Fx0FixtureError("fixture expected status is invalid")
        if self.expected_status == "PASS" and self.expected_error_code is not None:
            raise Fx0FixtureError("passing fixture cannot expect an error")
        if self.expected_status == "FAIL_CLOSED" and (
            self.expected_error_code not in _ALLOWED_FAILURES
        ):
            raise Fx0FixtureError("fail-closed fixture error is not preregistered")
        object.__setattr__(
            self, "source", _copy_mapping(self.source, label="fixture source")
        )
        object.__setattr__(
            self,
            "expected_canonical_logical_state",
            _copy_mapping(
                self.expected_canonical_logical_state,
                label="expected canonical logical state",
            ),
        )
        object.__setattr__(
            self,
            "expected_publication_history",
            _copy_sequence_of_mappings(
                self.expected_publication_history,
                label="expected publication history",
            ),
        )

    def execution_input(self) -> Fx0ExecutionCase:
        """Project the fixture to the oracle-free adapter input."""

        return Fx0ExecutionCase(
            case_id=self.case_id,
            source_sequence=self.source_sequence,
            source=self.source,
        )


@dataclass(frozen=True)
class MechanismOutcome:
    """Sanitized semantic outcome returned by a production-path adapter."""

    case_id: str
    status: str
    error_code: str | None
    canonical_logical_state: Mapping[str, Any]
    publication_history: Sequence[Mapping[str, Any]]

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id:
            raise Fx0FixtureError("mechanism outcome case id is invalid")
        if self.status not in _ALLOWED_STATUS:
            raise Fx0FixtureError("mechanism outcome status is invalid")
        if self.status == "PASS" and self.error_code is not None:
            raise Fx0FixtureError("passing mechanism outcome cannot contain an error")
        if self.status == "FAIL_CLOSED" and self.error_code not in _ALLOWED_FAILURES:
            raise Fx0FixtureError("mechanism outcome failure is not preregistered")
        object.__setattr__(
            self,
            "canonical_logical_state",
            _copy_mapping(
                self.canonical_logical_state,
                label="observed canonical logical state",
            ),
        )
        object.__setattr__(
            self,
            "publication_history",
            _copy_sequence_of_mappings(
                self.publication_history, label="observed publication history"
            ),
        )


@dataclass(frozen=True)
class Fx0FixtureSpec:
    """Hash-bound offline framework specification."""

    run_id: str
    parent_protocol_sha256: str
    amendment_sha256: str
    current_stage_pointer_sha256: str
    production_path_identity: Mapping[str, Any]
    cases: Sequence[Fx0FixtureCase]
    legacy_authority_inheritance: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            raise Fx0FixtureError("FX0 run id must be nonempty")
        _sha(self.parent_protocol_sha256, field_name="parent protocol")
        _sha(self.amendment_sha256, field_name="amendment")
        _sha(self.current_stage_pointer_sha256, field_name="current pointer")
        identity = _copy_mapping(
            self.production_path_identity, label="production path identity"
        )
        if set(identity) != {"status", "method", "identity_sha256"}:
            raise Fx0FixtureError("production path identity shape drift")
        if identity.get("method") != "M_STAR":
            raise Fx0FixtureError("production path identity method drift")
        status = identity.get("status")
        identity_sha = identity.get("identity_sha256")
        if status == "PLACEHOLDER_NOT_FROZEN":
            if identity_sha is not None:
                raise Fx0FixtureError("placeholder production identity has a hash")
        elif status == "FROZEN":
            _sha(identity_sha, field_name="production path identity")
        else:
            raise Fx0FixtureError("production path identity status drift")
        if self.legacy_authority_inheritance is not False:
            raise Fx0FixtureError("legacy authority inheritance is forbidden")
        if isinstance(self.cases, (str, bytes)) or not isinstance(
            self.cases, Sequence
        ):
            raise Fx0FixtureError("FX0 cases must be a sequence")
        cases = tuple(self.cases)
        if not cases or any(not isinstance(case, Fx0FixtureCase) for case in cases):
            raise Fx0FixtureError("FX0 cases are missing or malformed")
        case_ids = [case.case_id for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise Fx0FixtureError("FX0 fixture case ids must be unique")
        transitions = {case.transition for case in cases}
        missing = set(FX0_REQUIRED_TRANSITIONS) - transitions
        if missing:
            raise Fx0FixtureError("FX0 transition coverage is incomplete")
        conflicting = {
            (case.expected_status, case.expected_error_code)
            for case in cases
            if case.transition == "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED"
        }
        if ("FAIL_CLOSED", "CONFLICTING_DUPLICATE_UUID") not in conflicting:
            raise Fx0FixtureError("conflicting duplicate fail-closed coverage is missing")
        publication_failures = {
            case.expected_error_code
            for case in cases
            if case.transition == "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION"
        }
        if set(FX0_REQUIRED_FAILURE_MODES) - publication_failures:
            raise Fx0FixtureError("lost/duplicate/partial detection coverage is incomplete")
        object.__setattr__(self, "production_path_identity", identity)
        object.__setattr__(self, "cases", cases)


def _production_adapter(spec: Fx0FixtureSpec, mechanism: object) -> Any:
    identity = getattr(mechanism, "production_path_identity", None)
    if not isinstance(identity, Mapping):
        raise Fx0FixtureError("production path identity is missing")
    if not _canonical_equal(dict(identity), spec.production_path_identity):
        raise Fx0FixtureError("production path identity does not match fixture")
    execute = getattr(mechanism, "execute_fixture_case", None)
    if not callable(execute):
        raise Fx0FixtureError("production path fixture adapter is missing")
    return execute


async def run_fx0_fixture_async(
    spec: Fx0FixtureSpec, mechanism: object
) -> dict[str, Any]:
    """Execute every transition through the injected production-path adapter."""

    if not isinstance(spec, Fx0FixtureSpec):
        raise Fx0FixtureError("FX0 fixture specification is invalid")
    execute = _production_adapter(spec, mechanism)
    results: list[dict[str, Any]] = []
    for case in spec.cases:
        observed = execute(case.execution_input(), case.providers)
        if inspect.isawaitable(observed):
            observed = await observed
        if not isinstance(observed, MechanismOutcome):
            raise Fx0FixtureError(f"{case.case_id}: production outcome is malformed")
        if observed.case_id != case.case_id:
            raise Fx0FixtureError(f"{case.case_id}: outcome identity drift")
        if (
            observed.status != case.expected_status
            or observed.error_code != case.expected_error_code
        ):
            raise Fx0FixtureError(f"{case.case_id}: fail-closed outcome parity failed")
        if not _canonical_equal(
            observed.canonical_logical_state,
            case.expected_canonical_logical_state,
        ):
            raise Fx0FixtureError(
                f"{case.case_id}: canonical logical state parity failed"
            )
        if not _canonical_equal(
            observed.publication_history,
            case.expected_publication_history,
        ):
            raise Fx0FixtureError(
                f"{case.case_id}: publication history parity failed"
            )
        results.append(
            {
                "case_id": case.case_id,
                "transition": case.transition,
                "expected_status": case.expected_status,
                "expected_error_code": case.expected_error_code,
                "source_sequence": case.source_sequence,
                "source_sha256": payload_sha256(case.source),
                "controlled_provider_sha256": case.providers.hash_projection(),
                "canonical_logical_state_sha256": payload_sha256(
                    observed.canonical_logical_state
                ),
                "publication_history_sha256": payload_sha256(
                    observed.publication_history
                ),
                "exact_canonical_state_parity": True,
                "exact_publication_history_parity": True,
            }
        )
    return {
        "framework_verdict": "HARNESS_SELF_TEST_PASS",
        "fixture_count_policy": FIXTURE_COUNT_POLICY,
        "fixture_count": len(spec.cases),
        "covered_transitions": sorted({case.transition for case in spec.cases}),
        "covered_publication_failure_modes": sorted(
            {
                case.expected_error_code
                for case in spec.cases
                if case.expected_error_code in FX0_REQUIRED_FAILURE_MODES
            }
        ),
        "case_results": results,
    }


def run_fx0_fixture(spec: Fx0FixtureSpec, mechanism: object) -> dict[str, Any]:
    """Synchronous entry point; async production adapters remain supported."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_fx0_fixture_async(spec, mechanism))
    raise Fx0FixtureError(
        "run_fx0_fixture cannot run inside an event loop; use run_fx0_fixture_async"
    )


def build_fx0_artifact(
    spec: Fx0FixtureSpec,
    mechanism: object,
    *,
    git_commit: str,
) -> dict[str, Any]:
    """Run and seal framework evidence without granting method/live authority."""

    result = run_fx0_fixture(spec, mechanism)
    case_results = result["case_results"]
    payload = {
        "schema_version": SCHEMA,
        "lane": LANE,
        "framework_verdict": result["framework_verdict"],
        "framework_evidence_scope": "HARNESS_SELF_TEST_WITH_TEST_DOUBLE_ONLY",
        "fixture_count_policy": result["fixture_count_policy"],
        "fixture_count": result["fixture_count"],
        "covered_transitions": result["covered_transitions"],
        "covered_publication_failure_modes": result[
            "covered_publication_failure_modes"
        ],
        "controlled_nondeterminism_providers": list(CONTROLLED_PROVIDER_NAMES),
        "only_controlled_nondeterminism_may_be_stubbed": True,
        "production_mechanism_path_required": True,
        "production_path_identity": deepcopy(dict(spec.production_path_identity)),
        "input_bindings": {
            "parent_protocol_sha256": spec.parent_protocol_sha256,
            "amendment_sha256": spec.amendment_sha256,
            "current_stage_pointer_sha256": spec.current_stage_pointer_sha256,
        },
        "case_results": case_results,
        "case_results_sha256": payload_sha256(case_results),
        "exact_canonical_logical_state_parity": True,
        "exact_publication_history_parity": True,
        "legacy_authority_inheritance": False,
        "performance_claims_authorized": False,
        "semantic_correctness_claims_authorized": False,
        "m_star_mechanism_correctness_claim_authorized": False,
        "m_star_exact_parity_qualification": "NOT_EXECUTED",
        "m_star_exact_parity_reason": "PRODUCTION_IDENTITY_NOT_FROZEN",
        "authority": deepcopy(_AUTHORITY),
    }
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=spec.run_id,
    )
    return verify_fx0_artifact(artifact)


def _reject_private(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_ARTIFACT_KEYS:
                raise Fx0FixtureError("FX0 artifact contains private fixture data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def verify_fx0_artifact(value: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed on artifact tampering or accidental authority expansion."""

    artifact = _copy_mapping(value, label="FX0 artifact")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise Fx0FixtureError("FX0 artifact envelope shape drift")
    payload = _copy_mapping(artifact.get("payload"), label="FX0 payload")
    expected_keys = {
        "schema_version",
        "lane",
        "framework_verdict",
        "framework_evidence_scope",
        "fixture_count_policy",
        "fixture_count",
        "covered_transitions",
        "covered_publication_failure_modes",
        "controlled_nondeterminism_providers",
        "only_controlled_nondeterminism_may_be_stubbed",
        "production_mechanism_path_required",
        "production_path_identity",
        "input_bindings",
        "case_results",
        "case_results_sha256",
        "exact_canonical_logical_state_parity",
        "exact_publication_history_parity",
        "legacy_authority_inheritance",
        "performance_claims_authorized",
        "semantic_correctness_claims_authorized",
        "m_star_mechanism_correctness_claim_authorized",
        "m_star_exact_parity_qualification",
        "m_star_exact_parity_reason",
        "authority",
    }
    if (
        set(payload) != expected_keys
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or payload.get("schema_version") != SCHEMA
        or payload.get("lane") != LANE
        or payload.get("framework_verdict") != "HARNESS_SELF_TEST_PASS"
        or payload.get("framework_evidence_scope")
        != "HARNESS_SELF_TEST_WITH_TEST_DOUBLE_ONLY"
        or payload.get("fixture_count_policy") != FIXTURE_COUNT_POLICY
    ):
        raise Fx0FixtureError("FX0 artifact identity or envelope drift")
    bindings = _copy_mapping(payload.get("input_bindings"), label="FX0 bindings")
    if set(bindings) != {
        "parent_protocol_sha256",
        "amendment_sha256",
        "current_stage_pointer_sha256",
    }:
        raise Fx0FixtureError("FX0 input binding shape drift")
    for name, digest in bindings.items():
        _sha(digest, field_name=name)
    identity = _copy_mapping(
        payload.get("production_path_identity"), label="production path identity"
    )
    if identity != {
        "status": "PLACEHOLDER_NOT_FROZEN",
        "method": "M_STAR",
        "identity_sha256": None,
    }:
        raise Fx0FixtureError("unfrozen FX0 production identity drift")
    cases = payload.get("case_results")
    if isinstance(cases, (str, bytes)) or not isinstance(cases, Sequence):
        raise Fx0FixtureError("FX0 case results are malformed")
    rows = [_copy_mapping(item, label="FX0 case result") for item in cases]
    if (
        payload.get("fixture_count") != len(rows)
        or payload.get("case_results_sha256") != payload_sha256(rows)
        or set(payload.get("covered_transitions", ())) != set(FX0_REQUIRED_TRANSITIONS)
        or set(payload.get("covered_publication_failure_modes", ()))
        != set(FX0_REQUIRED_FAILURE_MODES)
        or payload.get("controlled_nondeterminism_providers")
        != list(CONTROLLED_PROVIDER_NAMES)
        or payload.get("only_controlled_nondeterminism_may_be_stubbed") is not True
        or payload.get("production_mechanism_path_required") is not True
        or payload.get("exact_canonical_logical_state_parity") is not True
        or payload.get("exact_publication_history_parity") is not True
        or payload.get("legacy_authority_inheritance") is not False
        or payload.get("performance_claims_authorized") is not False
        or payload.get("semantic_correctness_claims_authorized") is not False
        or payload.get("m_star_mechanism_correctness_claim_authorized") is not False
        or payload.get("m_star_exact_parity_qualification") != "NOT_EXECUTED"
        or payload.get("m_star_exact_parity_reason")
        != "PRODUCTION_IDENTITY_NOT_FROZEN"
        or payload.get("authority") != _AUTHORITY
    ):
        raise Fx0FixtureError("FX0 fixture evidence or authority drift")
    if len(rows) <= len(FX0_REQUIRED_TRANSITIONS):
        raise Fx0FixtureError("FX0 failure-mode transition coverage is incomplete")
    case_ids = [row.get("case_id") for row in rows]
    if (
        any(not isinstance(case_id, str) or not case_id for case_id in case_ids)
        or len(case_ids) != len(set(case_ids))
    ):
        raise Fx0FixtureError("FX0 case result identities are invalid")
    observed_transitions = {row.get("transition") for row in rows}
    observed_failure_modes = {
        row.get("expected_error_code")
        for row in rows
        if row.get("expected_error_code") in FX0_REQUIRED_FAILURE_MODES
    }
    observed_conflicting = {
        row.get("expected_error_code")
        for row in rows
        if row.get("transition") == "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED"
    }
    if observed_transitions != set(FX0_REQUIRED_TRANSITIONS):
        raise Fx0FixtureError("FX0 case coverage does not match transition inventory")
    if observed_failure_modes != set(FX0_REQUIRED_FAILURE_MODES):
        raise Fx0FixtureError("FX0 case coverage does not match failure inventory")
    if "CONFLICTING_DUPLICATE_UUID" not in observed_conflicting:
        raise Fx0FixtureError("FX0 case coverage misses conflicting duplicate failure")
    for row in rows:
        if set(row) != {
            "case_id",
            "transition",
            "expected_status",
            "expected_error_code",
            "source_sequence",
            "source_sha256",
            "controlled_provider_sha256",
            "canonical_logical_state_sha256",
            "publication_history_sha256",
            "exact_canonical_state_parity",
            "exact_publication_history_parity",
        }:
            raise Fx0FixtureError("FX0 case result shape drift")
        for name in (
            "source_sha256",
            "canonical_logical_state_sha256",
            "publication_history_sha256",
        ):
            _sha(row.get(name), field_name=f"case {name}")
        providers = _copy_mapping(
            row.get("controlled_provider_sha256"), label="provider hashes"
        )
        if set(providers) != {
            "llm_responses_sha256",
            "embeddings_sha256",
            "logical_times_sha256",
            "initial_graph_state_sha256",
            "candidate_sets_sha256",
        }:
            raise Fx0FixtureError("FX0 provider hash shape drift")
        for name, digest in providers.items():
            _sha(digest, field_name=f"case {name}")
        source_sequence = row.get("source_sequence")
        status = row.get("expected_status")
        error_code = row.get("expected_error_code")
        if (
            row.get("transition") not in FX0_REQUIRED_TRANSITIONS
            or isinstance(source_sequence, bool)
            or not isinstance(source_sequence, int)
            or source_sequence < 0
            or status not in _ALLOWED_STATUS
            or (status == "PASS" and error_code is not None)
            or (status == "FAIL_CLOSED" and error_code not in _ALLOWED_FAILURES)
            or (
                row.get("transition")
                == "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED"
                and (status, error_code)
                != ("FAIL_CLOSED", "CONFLICTING_DUPLICATE_UUID")
            )
            or (
                error_code in FX0_REQUIRED_FAILURE_MODES
                and (
                    row.get("transition")
                    != "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION"
                    or status != "FAIL_CLOSED"
                )
            )
            or row.get("exact_canonical_state_parity") is not True
            or row.get("exact_publication_history_parity") is not True
        ):
            raise Fx0FixtureError("FX0 case parity or identity drift")
    _reject_private(payload)
    artifact["payload"] = payload
    return artifact
