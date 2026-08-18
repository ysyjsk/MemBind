"""Offline feasibility contract for validated NodeResolve speculation.

This module is intentionally independent from the v3.1 coordinator.  It
answers a narrow question: if a NodeResolve call is materialized against a
stale state and later against the exact predecessor, can the two semantic
requests be compared deterministically and either reused or re-executed?

The fingerprint contains request semantics and candidate binding context, but
not the state-version label itself.  A state version is provenance for the
speculative/exact pair; candidate UUIDs, canonical projections, ordering, and
the rendered/tokenized request capture the semantic consequences of that
state.  A mismatch always falls back to exact execution.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_eval.artifacts import canonical_bytes, payload_sha256, sha256_file


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = "membind.paper-eval-v3.node-resolve-semantic-call.v1"
_FINGERPRINT_SCHEMA = "membind.paper-eval-v3.node-resolve-semantic-call-fingerprint.v1"
_REQUIRED_TRACE_FIELDS = (
    "operator_identity",
    "state_version",
    "candidate_order",
    "candidate_binding",
    "semantic_call_fingerprint",
)


class NodeResolveSpeculationError(ValueError):
    """A semantic-call record is malformed or cannot be reused safely."""


def _fail(code: str) -> NodeResolveSpeculationError:
    return NodeResolveSpeculationError(code)


def _nonnegative(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _mapping(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    try:
        encoded = canonical_bytes(dict(value)).decode("utf-8")
        decoded = json.loads(encoded)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(decoded, dict):
        raise _fail(code)
    return decoded


def _mapping_sequence(value: object, code: str) -> tuple[dict[str, object], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    result: list[dict[str, object]] = []
    for item in value:
        result.append(_mapping(item, code))
    return tuple(result)


def _candidate_sequence(value: object, code: str) -> tuple[str | int, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    result: list[str | int] = []
    for item in value:
        if isinstance(item, bool) or not (
            (isinstance(item, int) and item >= 0)
            or (isinstance(item, str) and bool(item))
        ):
            raise _fail(code)
        result.append(item)
    if len(set(result)) != len(result):
        raise _fail(code)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class SemanticCall:
    """A content-free, deterministic identity for one NodeResolve request."""

    source_sequence: int
    state_version: int
    rendered_request_sha256: str
    token_sequence_sha256: str
    response_schema: dict[str, object]
    model_identity: dict[str, object]
    decoding_identity: dict[str, object]
    operator_revision: str
    candidate_order: tuple[str | int, ...]
    candidate_bindings: tuple[dict[str, object], ...]
    extracted_node_mapping: dict[str, object]
    binding_context: dict[str, object]
    prompt_tokens: int
    service_span_ns: int

    @classmethod
    def create(
        cls,
        *,
        source_sequence: int,
        state_version: int,
        rendered_request_sha256: str,
        token_sequence_sha256: str,
        response_schema: Mapping[str, object],
        model_identity: Mapping[str, object],
        decoding_identity: Mapping[str, object],
        operator_revision: str,
        candidate_order: Sequence[str | int],
        candidate_bindings: Sequence[Mapping[str, object]],
        extracted_node_mapping: Mapping[str, object],
        binding_context: Mapping[str, object],
        prompt_tokens: int,
        service_span_ns: int,
    ) -> "SemanticCall":
        if not isinstance(operator_revision, str) or not operator_revision:
            raise _fail("operator_revision_invalid")
        order = _candidate_sequence(candidate_order, "candidate_order_invalid")
        bindings = _mapping_sequence(candidate_bindings, "candidate_bindings_invalid")
        binding_ids: list[str | int] = []
        for binding in bindings:
            candidate_id = binding.get("candidate_id")
            uuid = binding.get("uuid")
            if isinstance(candidate_id, bool) or not (
                (isinstance(candidate_id, int) and candidate_id >= 0)
                or (isinstance(candidate_id, str) and bool(candidate_id))
            ):
                raise _fail("candidate_binding_invalid")
            if not isinstance(uuid, str) or not uuid:
                raise _fail("candidate_binding_invalid")
            if not isinstance(binding.get("projection"), Mapping):
                raise _fail("candidate_binding_invalid")
            binding_ids.append(candidate_id)
        if tuple(binding_ids) != order:
            raise _fail("candidate_binding_order_mismatch")
        return cls(
            source_sequence=_nonnegative(source_sequence, "source_sequence_invalid"),
            state_version=_nonnegative(state_version, "state_version_invalid"),
            rendered_request_sha256=_sha(rendered_request_sha256, "rendered_request_sha256_invalid"),
            token_sequence_sha256=_sha(token_sequence_sha256, "token_sequence_sha256_invalid"),
            response_schema=_mapping(response_schema, "response_schema_invalid"),
            model_identity=_mapping(model_identity, "model_identity_invalid"),
            decoding_identity=_mapping(decoding_identity, "decoding_identity_invalid"),
            operator_revision=operator_revision,
            candidate_order=order,
            candidate_bindings=bindings,
            extracted_node_mapping=_mapping(extracted_node_mapping, "extracted_node_mapping_invalid"),
            binding_context=_mapping(binding_context, "binding_context_invalid"),
            prompt_tokens=_nonnegative(prompt_tokens, "prompt_tokens_invalid"),
            service_span_ns=_nonnegative(service_span_ns, "service_span_ns_invalid"),
        )

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "schema_version": _FINGERPRINT_SCHEMA,
            "rendered_request_sha256": self.rendered_request_sha256,
            "token_sequence_sha256": self.token_sequence_sha256,
            "response_schema": self.response_schema,
            "model_identity": self.model_identity,
            "decoding_identity": self.decoding_identity,
            "operator_revision": self.operator_revision,
            "candidate_order": list(self.candidate_order),
            "candidate_bindings": list(self.candidate_bindings),
            "extracted_node_mapping": self.extracted_node_mapping,
            "binding_context": self.binding_context,
        }

    @property
    def fingerprint(self) -> str:
        return payload_sha256(self.fingerprint_payload())

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA,
            "source_sequence": self.source_sequence,
            "state_version": self.state_version,
            "rendered_request_sha256": self.rendered_request_sha256,
            "token_sequence_sha256": self.token_sequence_sha256,
            "response_schema": self.response_schema,
            "model_identity": self.model_identity,
            "decoding_identity": self.decoding_identity,
            "operator_revision": self.operator_revision,
            "candidate_order": list(self.candidate_order),
            "candidate_bindings": list(self.candidate_bindings),
            "extracted_node_mapping": self.extracted_node_mapping,
            "binding_context": self.binding_context,
            "prompt_tokens": self.prompt_tokens,
            "service_span_ns": self.service_span_ns,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_record(cls, value: Mapping[str, object]) -> "SemanticCall":
        if not isinstance(value, Mapping) or value.get("schema_version") != _SCHEMA:
            raise _fail("semantic_call_schema_invalid")
        try:
            selected = cls.create(
                source_sequence=value["source_sequence"],
                state_version=value["state_version"],
                rendered_request_sha256=value["rendered_request_sha256"],
                token_sequence_sha256=value["token_sequence_sha256"],
                response_schema=value["response_schema"],
                model_identity=value["model_identity"],
                decoding_identity=value["decoding_identity"],
                operator_revision=value["operator_revision"],
                candidate_order=value["candidate_order"],
                candidate_bindings=value["candidate_bindings"],
                extracted_node_mapping=value["extracted_node_mapping"],
                binding_context=value["binding_context"],
                prompt_tokens=value["prompt_tokens"],
                service_span_ns=value["service_span_ns"],
            )
        except KeyError:
            raise _fail("semantic_call_field_missing") from None
        if value.get("fingerprint") != selected.fingerprint:
            raise _fail("fingerprint_mismatch")
        return selected


@dataclass(frozen=True, slots=True)
class SpeculationDecision:
    decision: str
    reason: str
    speculative_fingerprint: str
    exact_fingerprint: str
    avoided_exact_service_span_ns: int


def validate_speculation(speculative: SemanticCall, exact: SemanticCall) -> SpeculationDecision:
    """Validate a one-version-ahead speculation and fail closed on bad pairing."""

    if not isinstance(speculative, SemanticCall) or not isinstance(exact, SemanticCall):
        raise _fail("semantic_call_type_invalid")
    if speculative.source_sequence != exact.source_sequence:
        raise _fail("source_sequence_mismatch")
    if speculative.state_version >= exact.state_version:
        raise _fail("state_order_invalid")
    if speculative.fingerprint == exact.fingerprint:
        return SpeculationDecision(
            decision="REUSE",
            reason="SEMANTIC_CALL_FINGERPRINT_MATCH",
            speculative_fingerprint=speculative.fingerprint,
            exact_fingerprint=exact.fingerprint,
            avoided_exact_service_span_ns=exact.service_span_ns,
        )
    return SpeculationDecision(
        decision="REEXECUTE",
        reason="SEMANTIC_CALL_FINGERPRINT_MISMATCH",
        speculative_fingerprint=speculative.fingerprint,
        exact_fingerprint=exact.fingerprint,
        avoided_exact_service_span_ns=0,
    )


def analyze_replay(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Reduce paired offline calls without making a performance claim."""

    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence) or not records:
        raise _fail("replay_records_invalid")
    decisions: list[SpeculationDecision] = []
    prompt_total = service_total = 0
    prompt_reused = service_reused = avoided = 0
    for raw in records:
        if not isinstance(raw, Mapping):
            raise _fail("replay_record_invalid")
        try:
            speculative = SemanticCall.from_record(raw["speculative"])
            exact = SemanticCall.from_record(raw["exact"])
        except KeyError:
            raise _fail("replay_pair_missing") from None
        decision = validate_speculation(speculative, exact)
        decisions.append(decision)
        prompt_total += exact.prompt_tokens
        service_total += exact.service_span_ns
        if decision.decision == "REUSE":
            prompt_reused += exact.prompt_tokens
            service_reused += exact.service_span_ns
            avoided += decision.avoided_exact_service_span_ns
    count = len(decisions)
    reuse = sum(item.decision == "REUSE" for item in decisions)
    return {
        "schema_version": "membind.paper-eval-v3.node-resolve-replay-diagnostic.v1",
        "status": "DIAGNOSTIC_ONLY",
        "eligible_count": count,
        "reuse_count": reuse,
        "reexecute_count": count - reuse,
        "call_weighted_reuse_rate": reuse / count,
        "prompt_weighted_reuse_rate": prompt_reused / prompt_total if prompt_total else None,
        "service_weighted_reuse_rate": service_reused / service_total if service_total else None,
        "avoided_exact_service_span_ns": avoided,
        "exact_prompt_tokens": prompt_total,
        "exact_service_span_ns": service_total,
    }


def evaluate_replay_effectiveness(
    records: Sequence[Mapping[str, object]],
    *,
    overlap_exposed_ns: int | None,
) -> dict[str, object]:
    """Classify whether validated speculation has positive net service work.

    ``state_parity`` must come from an exact-state shadow/serial oracle; a
    matching request fingerprint alone is not a correctness proof.  The net
    work comparison is against one exact NodeResolve call per pair:

    ``baseline - speculative_path = avoided_exact - speculation - validation``.

    This is a service-work gate, not an end-to-end wall-clock claim.  The
    separate ``overlap_exposed_ns`` field records whether a scheduler could
    actually overlap the speculative work with otherwise useful execution.
    """

    if overlap_exposed_ns is None:
        return {
            "schema_version": "membind.paper-eval-v3.node-resolve-effectiveness.v1",
            "status": "DIAGNOSTIC_ONLY",
            "decision": "D2_DATA_INSUFFICIENT",
            "correctness_gate": "NOT_MEASURED",
            "missing_fields": ["overlap_exposed_ns"],
            "net_saved_service_work_ns": None,
        }
    try:
        overlap = _nonnegative(overlap_exposed_ns, "overlap_exposed_ns_invalid")
    except NodeResolveSpeculationError:
        raise
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence) or not records:
        raise _fail("replay_records_invalid")

    missing: set[str] = set()
    parsed: list[tuple[SemanticCall, SemanticCall, SpeculationDecision, bool, int]] = []
    for raw in records:
        if not isinstance(raw, Mapping):
            raise _fail("replay_record_invalid")
        for field in ("state_parity", "validation_overhead_ns"):
            if field not in raw:
                missing.add(field)
        try:
            speculative = SemanticCall.from_record(raw["speculative"])
            exact = SemanticCall.from_record(raw["exact"])
        except KeyError:
            raise _fail("replay_pair_missing") from None
        parity = raw.get("state_parity")
        overhead = raw.get("validation_overhead_ns")
        if not isinstance(parity, bool):
            missing.add("state_parity")
        if isinstance(overhead, bool) or not isinstance(overhead, int) or overhead < 0:
            missing.add("validation_overhead_ns")
        if isinstance(parity, bool) and isinstance(overhead, int) and overhead >= 0:
            parsed.append(
                (
                    speculative,
                    exact,
                    validate_speculation(speculative, exact),
                    parity,
                    overhead,
                )
            )
    if missing:
        return {
            "schema_version": "membind.paper-eval-v3.node-resolve-effectiveness.v1",
            "status": "DIAGNOSTIC_ONLY",
            "decision": "D2_DATA_INSUFFICIENT",
            "correctness_gate": "NOT_MEASURED",
            "missing_fields": sorted(missing),
            "net_saved_service_work_ns": None,
        }

    exact_total = sum(exact.service_span_ns for _spec, exact, _decision, _parity, _overhead in parsed)
    speculative_total = sum(spec.service_span_ns for spec, _exact, _decision, _parity, _overhead in parsed)
    validation_total = sum(overhead for _spec, _exact, _decision, _parity, overhead in parsed)
    reuse = [item for item in parsed if item[2].decision == "REUSE"]
    avoided = sum(item[1].service_span_ns for item in reuse)
    net_saved = avoided - speculative_total - validation_total
    parity_pass = all(item[3] for item in parsed)
    reuse_rate = avoided / exact_total if exact_total else None
    if not parity_pass:
        decision = "D2_UNSAFE"
        correctness = "FAIL"
        net: int | None = None
    elif net_saved > 0:
        correctness = "PASS"
        net = net_saved
        decision = (
            "D2_REUSE_POTENTIAL_HIGH_BUT_NO_OVERLAP"
            if overlap == 0 and len(reuse) == len(parsed)
            else "D2_REUSE_POTENTIAL_SUPPORTED"
        )
    else:
        correctness = "PASS"
        net = net_saved
        decision = "D2_LOW_REUSE_POTENTIAL"
    return {
        "schema_version": "membind.paper-eval-v3.node-resolve-effectiveness.v1",
        "status": "DIAGNOSTIC_ONLY",
        "decision": decision,
        "correctness_gate": correctness,
        "missing_fields": [],
        "eligible_count": len(parsed),
        "reuse_count": len(reuse),
        "service_weighted_reuse_rate": reuse_rate,
        "exact_service_span_ns": exact_total,
        "speculative_service_span_ns": speculative_total,
        "validation_overhead_ns": validation_total,
        "avoided_exact_service_span_ns": avoided,
        "net_saved_service_work_ns": net,
        "overlap_exposed_ns": overlap,
    }


def _keys(value: object, output: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str):
                output.add(key)
            _keys(child, output)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _keys(child, output)


def audit_trace_fields(paths: Sequence[Path]) -> dict[str, object]:
    """Check whether immutable traces expose the fields D2 needs."""

    if isinstance(paths, (str, bytes)) or not isinstance(paths, Sequence) or not paths:
        raise _fail("trace_paths_invalid")
    observed: set[str] = set()
    row_count = 0
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            raise _fail("trace_missing")
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                raise _fail("trace_json_invalid") from None
            _keys(value, observed)
            row_count += 1
    missing = [field for field in _REQUIRED_TRACE_FIELDS if field not in observed]
    return {
        "schema_version": "membind.paper-eval-v3.node-resolve-trace-audit.v1",
        "status": "DIAGNOSTIC_ONLY",
        "trace_count": len(paths),
        "row_count": row_count,
        "observed_fields": sorted(observed),
        "required_fields": list(_REQUIRED_TRACE_FIELDS),
        "missing_fields": missing,
        "verdict": "D2_DATA_SUFFICIENT" if not missing else "D2_DATA_INSUFFICIENT",
    }


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _function_calls(node: ast.AsyncFunctionDef | ast.FunctionDef) -> set[str]:
    calls: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _call_name(child.func)
            if name:
                calls.add(name)
    return calls


def audit_graphiti_node_resolve_source(path: Path) -> dict[str, object]:
    """Verify that Graphiti exposes a side-effect-free NodeResolve LLM stage.

    This is a structural source audit, not proof that stale/exact calls are
    stable on a workload.  It establishes only that candidate materialization
    and expensive LLM inference can be wrapped independently.
    """

    target = Path(path)
    if not target.is_file():
        raise _fail("graphiti_source_missing")
    try:
        source = target.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, UnicodeError, SyntaxError):
        raise _fail("graphiti_source_invalid") from None
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    }
    required = {
        "_collect_candidate_nodes",
        "_resolve_with_llm",
        "resolve_extracted_nodes",
    }
    missing = sorted(required - set(functions))
    calls = {
        name: _function_calls(functions[name])
        for name in required
        if name in functions
    }
    entry = calls.get("resolve_extracted_nodes", set())
    materializer = calls.get("_collect_candidate_nodes", set())
    llm_stage = calls.get("_resolve_with_llm", set())
    candidate_separate = (
        not missing
        and any(name.endswith("_collect_candidate_nodes") for name in entry)
        and not any(name.endswith("generate_response") for name in materializer)
    )
    llm_separate = (
        not missing
        and any(name.endswith("_resolve_with_llm") for name in entry)
        and any(name.endswith("generate_response") for name in llm_stage)
    )
    persistent_terminals = {
        "save",
        "delete",
        "execute_query",
        "bulk_add_nodes",
        "bulk_add_edges",
        "node_similarity_search",
        "_semantic_candidate_search",
        "_collect_candidate_nodes",
    }
    observed_persistent = sorted(
        name for name in llm_stage if name.rsplit(".", 1)[-1] in persistent_terminals
    )
    llm_persistent_free = not missing and not observed_persistent
    feasible = candidate_separate and llm_separate and llm_persistent_free
    return {
        "schema_version": "membind.paper-eval-v3.node-resolve-source-boundary-audit.v1",
        "status": "DIAGNOSTIC_ONLY",
        "source_path": str(target),
        "source_sha256": sha256_file(target),
        "required_functions": sorted(required),
        "missing_functions": missing,
        "candidate_materialization_separate": candidate_separate,
        "llm_execution_separate": llm_separate,
        "llm_stage_persistent_effect_free": llm_persistent_free,
        "observed_persistent_calls_in_llm_stage": observed_persistent,
        "verdict": (
            "NODE_RESOLVE_BOUNDARY_FEASIBLE"
            if feasible
            else "NODE_RESOLVE_BOUNDARY_NOT_FEASIBLE"
        ),
    }


__all__ = [
    "NodeResolveSpeculationError",
    "SemanticCall",
    "SpeculationDecision",
    "analyze_replay",
    "audit_graphiti_node_resolve_source",
    "audit_trace_fields",
    "validate_speculation",
    "evaluate_replay_effectiveness",
]
