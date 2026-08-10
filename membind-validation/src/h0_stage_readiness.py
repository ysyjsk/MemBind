"""Stage-level, one-shot full-stack readiness for live H0-B and H0-C.

The caller supplies already-resolved bindings and credentials.  This module
does not read configuration or environment variables.  The construction
preflight owns the first state gate; authorization is checked again after all
three services are ready so workload execution cannot begin on stale state.
"""

from __future__ import annotations

import inspect
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping

from h0_embedding import H0EmbeddingAdapter
from h0_live_preflight import PROTOCOL_VERSION, run_h0_readiness_preflight
from h0_neo4j import H0Neo4jReadiness
from h0_runtime import (
    H0InfrastructureError,
    H0ManifestError,
    H0StateGateError,
    authorize_h0_live_entry,
    canonical_json_sha256,
)


_PHASES = frozenset({"H0-B", "H0-C"})
_CANDIDATES = frozenset({"Q1", "Q2", "Q3"})
_FORBIDDEN_EVENT_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "credentials",
        "messages",
        "password",
        "prompt",
        "raw_prompt",
        "raw_response",
        "request_headers",
        "response_body",
    }
)


def _manifest_failure(reason: str) -> H0ManifestError:
    return H0ManifestError(f"H0 stage readiness denied: {reason}")


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_EVENT_KEYS:
                return True
            if _contains_forbidden_key(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _validate_identity(
    authorization: Any,
    *,
    candidate_id: str,
    phase: str,
) -> dict[str, Any]:
    if not isinstance(authorization, Mapping):
        raise H0StateGateError("H0 stage authorization is not a mapping")
    if (
        authorization.get("candidate_id") != candidate_id
        or authorization.get("phase") != phase
    ):
        raise H0StateGateError("H0 stage authorization identity mismatch")
    return deepcopy(dict(authorization))


def _validate_component_result(
    value: Any,
    *,
    component: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or _contains_forbidden_key(value):
        raise _manifest_failure(f"{component}_readiness_result_invalid")
    return deepcopy(dict(value))


async def _maybe_close(
    resource: Any,
    *,
    primary_failure: BaseException | None,
) -> None:
    close = getattr(resource, "close", None)
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except BaseException as exc:
        if primary_failure is None:
            raise _manifest_failure("resource_cleanup_failure") from exc


async def run_h0_stage_readiness(
    *,
    state_path: str | Path,
    stage_attempt_id: str,
    candidate_id: str,
    phase: str,
    construction_credential_loader: Callable[[], Mapping[str, Any]],
    resolved_identity_loader: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    embedding_binding: Mapping[str, Any],
    embedding_credentials: Mapping[str, Any],
    neo4j_binding: Mapping[str, Any],
    neo4j_credentials: Mapping[str, Any],
    progress_sink: Callable[[dict[str, Any]], Any],
    authorization_checker: Callable[..., Any] = authorize_h0_live_entry,
    construction_readiness_runner: Callable[..., Any] = run_h0_readiness_preflight,
    embedding_adapter_factory: Callable[..., Any] = H0EmbeddingAdapter,
    neo4j_readiness_factory: Callable[..., Any] = H0Neo4jReadiness,
    construction_transport_factory: Callable[[], Any] | None = None,
    embedding_transport: Any = None,
    neo4j_driver_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run each stage readiness exactly once and persist safe checkpoints.

    The construction runner must invoke its supplied authorization checker
    exactly once before touching configuration or transport.  This preserves
    the gate-first invariant of :func:`run_h0_readiness_preflight` while also
    allowing this orchestrator to bind the final authorization recheck to the
    exact mapping observed at entry.
    """

    if not isinstance(stage_attempt_id, str) or not stage_attempt_id.strip():
        raise _manifest_failure("stage_attempt_id_invalid")
    if candidate_id not in _CANDIDATES or phase not in _PHASES:
        raise _manifest_failure("candidate_or_phase_invalid")
    for label, dependency in (
        ("construction_credential_loader", construction_credential_loader),
        ("resolved_identity_loader", resolved_identity_loader),
        ("progress_sink", progress_sink),
        ("authorization_checker", authorization_checker),
        ("construction_readiness_runner", construction_readiness_runner),
        ("embedding_adapter_factory", embedding_adapter_factory),
        ("neo4j_readiness_factory", neo4j_readiness_factory),
    ):
        if not callable(dependency):
            raise _manifest_failure(f"{label}_invalid")

    event_identity = {
        "schema_version": "membind.h0.stage-readiness-event.v1",
        "protocol_version": PROTOCOL_VERSION,
        "stage_attempt_id": stage_attempt_id,
        "candidate_id": candidate_id,
        "phase": phase,
        "candidate_advance_allowed": False,
    }

    def persist(check: str, component: str, evidence: Mapping[str, Any]) -> None:
        safe_evidence = _validate_component_result(evidence, component=component)
        event = {
            **safe_evidence,
            **event_identity,
            "check": check,
            "component": component,
        }
        sink_result = progress_sink(event)
        if inspect.isawaitable(sink_result):
            raise _manifest_failure("progress_sink_must_be_synchronous")

    initial_gate_calls = 0
    initial_authorization: dict[str, Any] | None = None

    def initial_gate(**kwargs: Any) -> dict[str, Any]:
        nonlocal initial_gate_calls, initial_authorization
        initial_gate_calls += 1
        if initial_gate_calls != 1:
            raise H0StateGateError(
                "H0 stage construction readiness repeated the initial gate"
            )
        authorization = authorization_checker(**kwargs)
        initial_authorization = _validate_identity(
            authorization,
            candidate_id=candidate_id,
            phase=phase,
        )
        return deepcopy(initial_authorization)

    def construction_progress(event: dict[str, Any]) -> None:
        if initial_gate_calls != 1 or initial_authorization is None:
            raise H0StateGateError(
                "H0 stage construction readiness touched progress before the gate"
            )
        if not isinstance(event, Mapping):
            raise _manifest_failure("construction_event_invalid")
        check = event.get("check")
        if not isinstance(check, str) or not check:
            raise _manifest_failure("construction_event_check_invalid")
        persist(check, "construction", event)

    construction_kwargs: dict[str, Any] = {
        "state_path": state_path,
        "stage_attempt_id": stage_attempt_id,
        "candidate_id": candidate_id,
        "phase": phase,
        "credential_loader": construction_credential_loader,
        "resolved_identity_loader": resolved_identity_loader,
        "authorization_checker": initial_gate,
        "progress_sink": construction_progress,
    }
    if construction_transport_factory is not None:
        construction_kwargs["transport_factory"] = construction_transport_factory
    construction_result = await construction_readiness_runner(**construction_kwargs)
    if initial_gate_calls != 1 or initial_authorization is None:
        raise H0StateGateError(
            "H0 stage construction readiness did not perform the initial gate"
        )
    construction = _validate_component_result(
        construction_result,
        component="construction",
    )
    if (
        construction.get("status") != "ready"
        or construction.get("authorized_candidate_execution_ready") is not True
        or construction.get("generation_requests") != 0
    ):
        raise _manifest_failure("construction_readiness_contract_failure")
    persist(
        "construction_ready",
        "construction",
        {
            "qualified": True,
            "generation_requests": 0,
            "construction_readiness_count": 1,
        },
    )

    embedding_kwargs: dict[str, Any] = {
        "binding": embedding_binding,
        "credentials": embedding_credentials,
    }
    if embedding_transport is not None:
        embedding_kwargs["transport"] = embedding_transport
    embedding = embedding_adapter_factory(**embedding_kwargs)
    embedding_failure: BaseException | None = None
    try:
        embedding_result = await embedding.readiness()
        embedding_evidence = _validate_component_result(
            embedding_result,
            component="embedding",
        )
        if (
            embedding_evidence.get("event") != "embedding_metadata_readiness"
            or embedding_evidence.get("request_count") != 3
            or embedding_evidence.get("http_attempt_count") != 3
            or embedding_evidence.get("embedding_request_count") != 0
            or embedding_evidence.get("llm_request_count") != 0
            or embedding_evidence.get("warmup_performed") is not False
        ):
            raise _manifest_failure("embedding_readiness_contract_failure")
        persist("embedding_ready", "embedding", embedding_evidence)
    except H0InfrastructureError as exc:
        embedding_failure = exc
        persist(
            "embedding_readiness_failure",
            "embedding",
            {
                "qualified": False,
                "failure_code": "embedding_unreachable",
                "embedding_readiness_count": 1,
                "generation_requests": 0,
                "embedding_request_count": 0,
                "llm_request_count": 0,
            },
        )
        raise
    except BaseException as exc:
        embedding_failure = exc
        raise
    finally:
        await _maybe_close(embedding, primary_failure=embedding_failure)

    neo4j_kwargs: dict[str, Any] = {
        "binding": neo4j_binding,
        "credentials": neo4j_credentials,
        "attempt_id": stage_attempt_id,
        "candidate": candidate_id,
        "phase": phase,
    }
    if neo4j_driver_factory is not None:
        neo4j_kwargs["driver_factory"] = neo4j_driver_factory
    neo4j = neo4j_readiness_factory(**neo4j_kwargs)
    neo4j_failure: BaseException | None = None
    try:
        neo4j_result = await neo4j.readiness()
        neo4j_evidence = _validate_component_result(
            neo4j_result,
            component="neo4j",
        )
        if (
            neo4j_evidence.get("readiness_code") != "pass"
            or neo4j_evidence.get("verify_connectivity_call_count") != 1
            or neo4j_evidence.get("cypher_call_count") != 0
            or neo4j_evidence.get("close_call_count") != 1
            or neo4j_evidence.get("failure_code") is not None
        ):
            raise _manifest_failure("neo4j_readiness_contract_failure")
        persist("neo4j_ready", "neo4j", neo4j_evidence)
    except H0InfrastructureError as exc:
        neo4j_failure = exc
        persist(
            "neo4j_readiness_failure",
            "neo4j",
            {
                "qualified": False,
                "failure_code": "neo4j_unreachable",
                "neo4j_readiness_count": 1,
                "generation_requests": 0,
                "cypher_call_count": 0,
            },
        )
        raise
    except BaseException as exc:
        neo4j_failure = exc
        raise
    finally:
        await _maybe_close(neo4j, primary_failure=neo4j_failure)

    final_authorization = _validate_identity(
        authorization_checker(
            state_path=state_path,
            candidate_id=candidate_id,
            phase=phase,
        ),
        candidate_id=candidate_id,
        phase=phase,
    )
    if final_authorization != initial_authorization:
        raise H0StateGateError("H0 stage authorization changed during readiness")
    authorization_sha256 = canonical_json_sha256(final_authorization)
    persist(
        "authorization_recheck",
        "authorization",
        {
            "qualified": True,
            "authorization_sha256": authorization_sha256,
            "authorization_recheck_count": 1,
        },
    )

    return {
        "schema_version": "membind.h0.stage-readiness.v1",
        "protocol_version": PROTOCOL_VERSION,
        "stage_attempt_id": stage_attempt_id,
        "candidate_id": candidate_id,
        "phase": phase,
        "status": "ready",
        "construction_readiness_count": 1,
        "embedding_readiness_count": 1,
        "neo4j_readiness_count": 1,
        "authorization_recheck_count": 1,
        "generation_requests": 0,
        "embedding_request_count": 0,
        "per_history_warmup_count": 0,
        "authorization_sha256": authorization_sha256,
        "candidate_advance_allowed": False,
        "secrets_persisted": False,
    }
