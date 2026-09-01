"""Finite structured-output schemas and deterministic request preflight."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence


RUNTIME_RELIABILITY_PROFILE = "shared-structured-output-recovery-v1"
SCHEMA_REVISION = "finite-edge-schema-v1"
RECOVERY_POLICY_REVISION = "classified-request-recovery-v1"
RECOVERY_POLICY_MAX_TRANSIENT_RETRIES = 2
RECOVERY_POLICY_MAX_TRUNCATION_VARIANTS = 0
RECOVERY_POLICY_MAX_CONTEXT_CORRECTIONS = 0


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class SchemaBoundednessReport:
    status: str
    schema_sha256: str
    visited_schema_nodes: int
    issues: tuple[SchemaIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "issues": [asdict(issue) for issue in self.issues],
        }


class SchemaBoundednessError(ValueError):
    def __init__(self, issues: Sequence[SchemaIssue]) -> None:
        selected = tuple(issues)
        self.issues = selected
        rendered = ", ".join(f"{issue.path}:{issue.reason}" for issue in selected)
        super().__init__(f"structured-output schema is not finite: {rendered}")


class StructuredOutputBudgetError(ValueError):
    pass


class StructuredOutputError(ValueError):
    """Base class for failures observed at the structured response seam."""


class StructuredOutputLengthTruncation(json.JSONDecodeError, StructuredOutputError):
    failure_class = "OUTPUT_LENGTH_TRUNCATION"

    def __init__(self, message: str = "structured response stopped at the length cap", **metadata: Any) -> None:
        position = int(metadata.get("response_characters", 0) or 0)
        json.JSONDecodeError.__init__(self, message, "", position)
        self.metadata = dict(metadata)


class StructuredOutputMalformed(json.JSONDecodeError, StructuredOutputError):
    failure_class = "MALFORMED_STRUCTURED_OUTPUT"

    def __init__(self, message: str, *, position: int | None = None, **metadata: Any) -> None:
        json.JSONDecodeError.__init__(
            self,
            message,
            str(metadata.get("doc", "")),
            int(position or 0),
        )
        self.position = position
        self.metadata = dict(metadata)


@dataclass(frozen=True, slots=True)
class RecoveryIdentity:
    """Three-level identity for one semantic request and its physical calls."""

    semantic_operation_id: str
    request_variant_id: str
    physical_attempt_id: str


@dataclass(frozen=True, slots=True)
class RecoveryAttempt:
    identity: RecoveryIdentity
    attempt_index: int
    failure_class: str | None
    status: str


def recovery_policy_document() -> dict[str, Any]:
    """Return the frozen V6.1 structured-output policy.

    Native serial and relaxed-order arms retain upstream Graphiti semantics;
    this recovery policy is therefore an identity artifact for the proposed
    V6.1 path, not a native-arm algorithm change.
    """

    return {
        "policy_revision": RECOVERY_POLICY_REVISION,
        "transient_extra_physical_attempts": RECOVERY_POLICY_MAX_TRANSIENT_RETRIES,
        "truncation_smaller_variants": RECOVERY_POLICY_MAX_TRUNCATION_VARIANTS,
        "context_budget_corrections": RECOVERY_POLICY_MAX_CONTEXT_CORRECTIONS,
        "malformed_replay": "none",
        "semantic_validation_retry": "none",
        "unknown_failure": "fail_closed",
        "transcript_capture": "final_valid_response_only",
    }


def recovery_policy_sha256() -> str:
    return hashlib.sha256(
        json.dumps(
            recovery_policy_document(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def reliability_identity() -> dict[str, str]:
    """Return the immutable policy identity shared by every construction arm."""

    return {
        "runtime_reliability_profile": RUNTIME_RELIABILITY_PROFILE,
        "schema_revision": SCHEMA_REVISION,
        "recovery_policy_revision": RECOVERY_POLICY_REVISION,
        "recovery_policy_sha256": recovery_policy_sha256(),
    }


def classify_exception_for_recovery(
    error: BaseException,
    *,
    finish_reason: str | None = None,
    response_present: bool = False,
    status_code: int | None = None,
) -> str:
    """Use the structured classifier for retry decisions."""

    return classify_structured_failure(
        finish_reason=finish_reason,
        error=error,
        response_present=response_present,
        status_code=status_code,
    )


class StructuredRecoveryController:
    """Bounded, explicit recovery for one semantic structured request.

    The controller never retries malformed or semantically invalid responses.
    The API retains deterministic variant factories, but the frozen policy
    sets both truncation variants and context corrections to zero.  Certified
    truncation and context failures therefore fail closed.  Only transient
    transport failures receive bounded physical retries under one stable
    request variant identity.
    """

    def __init__(
        self,
        *,
        semantic_operation_id: str,
        request_variant_id: str,
        attempt_sink: Callable[[RecoveryAttempt], None] | None = None,
    ) -> None:
        if not semantic_operation_id or not request_variant_id:
            raise ValueError("recovery identities must be non-empty")
        self.semantic_operation_id = str(semantic_operation_id)
        self.request_variant_id = str(request_variant_id)
        self.attempt_sink = attempt_sink
        self.attempts: list[RecoveryAttempt] = []

    async def run(
        self,
        operation: Callable[[str], Any],
        *,
        classify: Callable[[BaseException], str] | None = None,
        smaller_variant: Callable[[str], str | None] | None = None,
        context_variant: Callable[[str], str | None] | None = None,
    ) -> Any:
        import inspect

        variant = self.request_variant_id
        transient_retries = 0
        truncation_variants = 0
        context_corrections = 0
        attempt_index = 0
        while True:
            physical_id = f"{self.semantic_operation_id}:physical:{attempt_index}"
            identity = RecoveryIdentity(
                semantic_operation_id=self.semantic_operation_id,
                request_variant_id=variant,
                physical_attempt_id=physical_id,
            )
            try:
                result = operation(variant)
                if inspect.isawaitable(result):
                    result = await result
            except BaseException as exc:
                failure_class = (
                    classify(exc) if classify is not None else classify_exception_for_recovery(exc)
                )
                record = RecoveryAttempt(identity, attempt_index, failure_class, "failure")
                self.attempts.append(record)
                if self.attempt_sink is not None:
                    self.attempt_sink(record)
                if failure_class == "SERVER_TRANSIENT" or failure_class in {
                    "TRANSPORT_INCOMPLETE",
                }:
                    if transient_retries < RECOVERY_POLICY_MAX_TRANSIENT_RETRIES:
                        transient_retries += 1
                        attempt_index += 1
                        continue
                elif failure_class == "OUTPUT_LENGTH_TRUNCATION":
                    if smaller_variant is not None and truncation_variants < RECOVERY_POLICY_MAX_TRUNCATION_VARIANTS:
                        next_variant = smaller_variant(variant)
                        if next_variant is not None and next_variant != variant:
                            variant = str(next_variant)
                            truncation_variants += 1
                            attempt_index += 1
                            continue
                elif failure_class == "CONTEXT_BUDGET_EXHAUSTED":
                    if context_variant is not None and context_corrections < RECOVERY_POLICY_MAX_CONTEXT_CORRECTIONS:
                        next_variant = context_variant(variant)
                        if next_variant is not None and next_variant != variant:
                            variant = str(next_variant)
                            context_corrections += 1
                            attempt_index += 1
                            continue
                raise
            else:
                record = RecoveryAttempt(identity, attempt_index, None, "success")
                self.attempts.append(record)
                if self.attempt_sink is not None:
                    self.attempt_sink(record)
                return result


def classify_structured_failure(
    *,
    finish_reason: str | None = None,
    error: BaseException | None = None,
    response_present: bool = False,
    status_code: int | None = None,
) -> str:
    """Classify a response from transport metadata before inspecting JSON text."""

    reason = str(finish_reason or "").casefold()
    if reason in {"length", "max_tokens", "token_limit"}:
        return "OUTPUT_LENGTH_TRUNCATION"
    if isinstance(error, StructuredOutputLengthTruncation):
        return "OUTPUT_LENGTH_TRUNCATION"
    if status_code == 429 or (isinstance(status_code, int) and status_code >= 500):
        return "SERVER_TRANSIENT"
    text = str(error or "").casefold()
    if not response_present and any(
        marker in text
        for marker in ("connection", "timeout", "reset", "http", "body", "eof")
    ):
        return "TRANSPORT_INCOMPLETE"
    if "context length" in text or "maximum context" in text:
        return "CONTEXT_BUDGET_EXHAUSTED"
    if isinstance(error, (json.JSONDecodeError, StructuredOutputMalformed)):
        return "MALFORMED_STRUCTURED_OUTPUT"
    return "UNKNOWN_MISSING_FAILURE_EVIDENCE"


def parse_structured_content(
    content: str | None,
    *,
    finish_reason: str | None,
    max_tokens: int | None = None,
) -> Any:
    """Parse only a complete response; length stops are rejected first."""

    if str(finish_reason or "").casefold() in {"length", "max_tokens", "token_limit"}:
        raise StructuredOutputLengthTruncation(
            finish_reason=str(finish_reason),
            max_tokens=max_tokens,
            response_characters=0 if content is None else len(content),
        )
    if not isinstance(content, str) or not content.strip():
        raise StructuredOutputMalformed("structured response is empty", position=0)
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredOutputMalformed(
            "structured response is not valid JSON",
            position=exc.pos,
            line=exc.lineno,
            column=exc.colno,
            doc=text,
        ) from exc


@dataclass(frozen=True, slots=True)
class SchemaBoundCertificate:
    schema_sha256: str
    schema_worst_case_characters: int
    schema_worst_case_tokens: int
    output_token_bound_method: str
    tokenizer_witness_tokens: int | None
    exact_prompt_tokens: int
    effective_completion_budget: int
    configured_effective_max_tokens: int
    context_limit: int
    context_safety_margin: int
    status: str
    failure_reasons: tuple[str, ...]
    output_response_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "failure_reasons": list(self.failure_reasons)}


@dataclass(frozen=True, slots=True)
class EdgePageCapacitySelection:
    capacity: int
    certificate: SchemaBoundCertificate
    rejected_capacities: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SchemaCapacitySelection:
    """Largest finite schema capacity admitted by one completion budget."""

    capacity: int
    certificate: SchemaBoundCertificate
    rejected_capacities: tuple[int, ...]


def _canonical_schema(schema: Mapping[str, Any]) -> str:
    return json.dumps(schema, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def schema_sha256(schema: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_schema(schema).encode("utf-8")).hexdigest()


def _json_pointer(root: Mapping[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise KeyError(reference)
    value: Any = root
    for raw in reference[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, Mapping) or key not in value:
            raise KeyError(reference)
        value = value[key]
    return value


def _finite_literal(schema: Mapping[str, Any]) -> bool:
    enum = schema.get("enum")
    return "const" in schema or (isinstance(enum, list) and bool(enum))


def _finite_number(schema: Mapping[str, Any]) -> bool:
    if _finite_literal(schema):
        return True
    lower = schema.get("minimum", schema.get("exclusiveMinimum"))
    upper = schema.get("maximum", schema.get("exclusiveMaximum"))
    return (
        isinstance(lower, (int, float))
        and not isinstance(lower, bool)
        and math.isfinite(float(lower))
        and isinstance(upper, (int, float))
        and not isinstance(upper, bool)
        and math.isfinite(float(upper))
    )


def validate_schema_boundedness(
    schema: Mapping[str, Any], *, raise_on_error: bool = True
) -> SchemaBoundednessReport:
    """Recursively prove that every value admitted by ``schema`` is finite."""

    if not isinstance(schema, Mapping):
        raise TypeError("structured-output schema must be an object")
    issues: list[SchemaIssue] = []
    visited = 0
    active_references: set[str] = set()

    def issue(path: str, reason: str) -> None:
        issues.append(SchemaIssue(path or "$", reason))

    def visit(value: Any, path: str) -> None:
        nonlocal visited
        if not isinstance(value, Mapping):
            issue(path, "schema_node_not_object")
            return
        visited += 1
        reference = value.get("$ref")
        if isinstance(reference, str):
            if reference in active_references:
                issue(path, "recursive_reference_unbounded")
                return
            try:
                target = _json_pointer(schema, reference)
            except KeyError:
                issue(path, "reference_unresolved")
                return
            active_references.add(reference)
            visit(target, f"{path}.$ref({reference})")
            active_references.remove(reference)
            return

        for keyword in ("anyOf", "oneOf", "allOf"):
            branches = value.get(keyword)
            if branches is not None:
                if not isinstance(branches, list) or not branches:
                    issue(f"{path}.{keyword}", "branch_set_empty")
                else:
                    for index, branch in enumerate(branches):
                        visit(branch, f"{path}.{keyword}[{index}]")

        schema_type = value.get("type")
        if isinstance(schema_type, list):
            for index, member in enumerate(schema_type):
                visit({**value, "type": member}, f"{path}.type[{index}]")
            return
        if schema_type is None:
            if _finite_literal(value):
                return
            if any(
                isinstance(value.get(keyword), list) and bool(value.get(keyword))
                for keyword in ("anyOf", "oneOf", "allOf")
            ):
                return
            issue(path, "schema_type_missing")
            return
        if schema_type in {"null", "boolean"}:
            return
        if schema_type == "string":
            maximum = value.get("maxLength")
            if not _finite_literal(value) and not (
                isinstance(maximum, int) and not isinstance(maximum, bool) and maximum >= 0
            ):
                issue(path, "string_max_length_missing")
            return
        if schema_type == "array":
            maximum = value.get("maxItems")
            if not (
                isinstance(maximum, int) and not isinstance(maximum, bool) and maximum >= 0
            ):
                issue(path, "array_max_items_missing")
            items = value.get("items")
            if not isinstance(items, Mapping):
                issue(f"{path}.items", "array_items_schema_missing")
            else:
                visit(items, f"{path}.items")
            return
        if schema_type == "object":
            if value.get("additionalProperties") is not False:
                issue(path, "object_additional_properties_open")
            if value.get("patternProperties"):
                issue(path, "object_pattern_properties_open")
            properties = value.get("properties", {})
            if not isinstance(properties, Mapping):
                issue(f"{path}.properties", "object_properties_invalid")
                return
            for name, child in properties.items():
                visit(child, f"{path}.properties.{name}")
            return
        if schema_type in {"integer", "number"}:
            if not _finite_number(value):
                issue(path, "number_range_missing")
            return
        issue(path, "schema_type_unsupported")

    visit(schema, "$")
    report = SchemaBoundednessReport(
        status="PASS" if not issues else "FAIL",
        schema_sha256=schema_sha256(schema),
        visited_schema_nodes=visited,
        issues=tuple(issues),
    )
    if issues and raise_on_error:
        raise SchemaBoundednessError(issues)
    return report


def schema_worst_case_characters(schema: Mapping[str, Any]) -> int:
    """Return a conservative upper bound for compact ensure-ASCII JSON output."""

    validate_schema_boundedness(schema)
    active_references: set[str] = set()

    def bound(value: Mapping[str, Any]) -> int:
        reference = value.get("$ref")
        if isinstance(reference, str):
            if reference in active_references:
                raise SchemaBoundednessError(
                    [SchemaIssue(reference, "recursive_reference_unbounded")]
                )
            target = _json_pointer(schema, reference)
            active_references.add(reference)
            result = bound(target)
            active_references.remove(reference)
            return result
        if "const" in value:
            return len(json.dumps(value["const"], ensure_ascii=True, separators=(",", ":")))
        enum = value.get("enum")
        if isinstance(enum, list) and enum:
            return max(
                len(json.dumps(item, ensure_ascii=True, separators=(",", ":")))
                for item in enum
            )
        branches = [
            branch
            for keyword in ("anyOf", "oneOf")
            for branch in (value.get(keyword) or [])
            if isinstance(branch, Mapping)
        ]
        if branches:
            return max(bound(branch) for branch in branches)
        all_of = value.get("allOf")
        if isinstance(all_of, list) and all_of:
            return sum(bound(branch) for branch in all_of if isinstance(branch, Mapping))
        schema_type = value.get("type")
        if schema_type == "null":
            return 4
        if schema_type == "boolean":
            return 5
        if schema_type == "string":
            # Every Unicode code point is bounded by a six-character JSON escape.
            return 2 + 6 * int(value["maxLength"])
        if schema_type == "array":
            count = int(value["maxItems"])
            item = bound(value["items"])
            return 2 + count * item + max(0, count - 1)
        if schema_type == "object":
            properties = value.get("properties", {})
            members = [
                len(json.dumps(str(name), ensure_ascii=True)) + 1 + bound(child)
                for name, child in properties.items()
            ]
            return 2 + sum(members) + max(0, len(members) - 1)
        if schema_type in {"integer", "number"}:
            candidates = [
                value.get("minimum", value.get("exclusiveMinimum")),
                value.get("maximum", value.get("exclusiveMaximum")),
            ]
            return max(len(json.dumps(candidate)) for candidate in candidates)
        if isinstance(schema_type, list):
            return max(bound({**value, "type": member}) for member in schema_type)
        raise SchemaBoundednessError([SchemaIssue("$", "schema_type_unsupported")])

    return bound(schema)


def schema_worst_case_json(schema: Mapping[str, Any]) -> str:
    """Materialize a compact JSON witness that realizes every schema bound."""

    validate_schema_boundedness(schema)
    active_references: set[str] = set()

    def witness(value: Mapping[str, Any]) -> Any:
        reference = value.get("$ref")
        if isinstance(reference, str):
            if reference in active_references:
                raise SchemaBoundednessError(
                    [SchemaIssue(reference, "recursive_reference_unbounded")]
                )
            target = _json_pointer(schema, reference)
            active_references.add(reference)
            result = witness(target)
            active_references.remove(reference)
            return result
        if "const" in value:
            return value["const"]
        enum = value.get("enum")
        if isinstance(enum, list) and enum:
            return max(enum, key=lambda item: len(json.dumps(item, ensure_ascii=True)))
        for keyword in ("anyOf", "oneOf"):
            branches = value.get(keyword)
            if isinstance(branches, list) and branches:
                return max(
                    (witness(branch) for branch in branches if isinstance(branch, Mapping)),
                    key=lambda item: len(json.dumps(item, ensure_ascii=True)),
                )
        all_of = value.get("allOf")
        if isinstance(all_of, list) and all_of:
            result: dict[str, Any] = {}
            for branch in all_of:
                if isinstance(branch, Mapping) and isinstance(witness(branch), Mapping):
                    result.update(witness(branch))
            return result
        schema_type = value.get("type")
        if schema_type == "null":
            return None
        if schema_type == "boolean":
            return True
        if schema_type == "string":
            return "\x00" * int(value["maxLength"])
        if schema_type == "array":
            return [witness(value["items"]) for _ in range(int(value["maxItems"]))]
        if schema_type == "object":
            properties = value.get("properties", {})
            return {
                str(name): witness(child)
                for name, child in properties.items()
                if isinstance(child, Mapping)
            }
        if schema_type in {"integer", "number"}:
            return value.get("maximum", value.get("exclusiveMaximum", 0))
        if isinstance(schema_type, list):
            return witness({**value, "type": schema_type[0]})
        raise SchemaBoundednessError([SchemaIssue("$", "schema_type_unsupported")])

    return json.dumps(witness(schema), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def build_schema_bound_certificate(
    *,
    messages: Sequence[Any],
    schema: Mapping[str, Any],
    token_counter: Callable[[Sequence[Any]], int],
    context_limit: int,
    effective_max_tokens: int,
    safety_margin_tokens: int,
    output_token_counter: Callable[[str], int] | None = None,
) -> SchemaBoundCertificate:
    """Bind exact prompt tokens to a conservative finite output-token proof."""

    validate_schema_boundedness(schema)
    prompt_tokens = int(token_counter(messages))
    if prompt_tokens < 0:
        raise ValueError("prompt token count cannot be negative")
    if context_limit <= 0 or effective_max_tokens <= 0 or safety_margin_tokens < 0:
        raise ValueError("structured-output token budgets are invalid")
    characters = schema_worst_case_characters(schema)
    witness = schema_worst_case_json(schema)
    tokenizer_witness_tokens = (
        int(output_token_counter(witness)) if output_token_counter is not None else None
    )
    if tokenizer_witness_tokens is not None and tokenizer_witness_tokens < 0:
        raise ValueError("output token count cannot be negative")
    # The tokenizer witness is diagnostic evidence, not a proof of the maximum
    # BPE token count: another bounded string can segment into more tokens than
    # the selected witness.  ``ensure_ascii`` makes every witness byte an ASCII
    # character, and a byte-level tokenizer cannot emit more tokens than bytes,
    # so the serialized-character bound is conservative for every admitted
    # value.  Keep the exact witness count alongside that proof for auditing.
    output_tokens = characters
    bound_method = "one_token_per_compact_ensure_ascii_json_character_v2"
    context_available = max(0, context_limit - prompt_tokens - safety_margin_tokens)
    completion_budget = min(effective_max_tokens, context_available)
    failures: list[str] = []
    if effective_max_tokens < output_tokens:
        failures.append("completion_budget_below_schema_bound")
    if prompt_tokens + output_tokens + safety_margin_tokens > context_limit:
        failures.append("context_budget_exhausted")
    return SchemaBoundCertificate(
        schema_sha256=schema_sha256(schema),
        schema_worst_case_characters=characters,
        schema_worst_case_tokens=output_tokens,
        output_token_bound_method=bound_method,
        tokenizer_witness_tokens=tokenizer_witness_tokens,
        exact_prompt_tokens=prompt_tokens,
        effective_completion_budget=completion_budget,
        configured_effective_max_tokens=effective_max_tokens,
        context_limit=context_limit,
        context_safety_margin=safety_margin_tokens,
        status="PASS" if not failures else "FAIL",
        failure_reasons=tuple(failures),
        output_response_sha256=hashlib.sha256(witness.encode("utf-8")).hexdigest(),
    )


def choose_edge_page_capacity(
    *,
    messages: Sequence[Any],
    schemas_by_capacity: Mapping[int, Mapping[str, Any]],
    requested_capacity: int,
    token_counter: Callable[[Sequence[Any]], int],
    context_limit: int,
    effective_max_tokens: int,
    safety_margin_tokens: int,
    output_token_counter: Callable[[str], int] | None = None,
) -> EdgePageCapacitySelection:
    """Choose the largest preregistered finite edge page that passes preflight."""

    if requested_capacity < 1:
        raise ValueError("requested edge page capacity must be positive")
    rejected: list[int] = []
    for capacity in range(requested_capacity, 0, -1):
        schema = schemas_by_capacity.get(capacity)
        if schema is None:
            rejected.append(capacity)
            continue
        certificate = build_schema_bound_certificate(
            messages=messages,
            schema=schema,
            token_counter=token_counter,
            context_limit=context_limit,
            effective_max_tokens=effective_max_tokens,
            safety_margin_tokens=safety_margin_tokens,
            output_token_counter=output_token_counter,
        )
        if certificate.status == "PASS":
            return EdgePageCapacitySelection(
                capacity=capacity,
                certificate=certificate,
                rejected_capacities=tuple(rejected),
            )
        rejected.append(capacity)
    raise StructuredOutputBudgetError(
        "no finite edge page capacity fits the certified context and completion budget"
    )


def choose_node_schema_capacity(
    *,
    messages: Sequence[Any],
    schemas_by_capacity: Mapping[int, Mapping[str, Any]],
    requested_capacity: int,
    token_counter: Callable[[Sequence[Any]], int],
    context_limit: int,
    effective_max_tokens: int,
    safety_margin_tokens: int,
    output_token_counter: Callable[[str], int] | None = None,
) -> SchemaCapacitySelection:
    """Choose the largest finite node schema that fits the actual request budget.

    Turn-partitioned extraction deliberately uses a smaller completion budget
    than the client-wide 32768-token setting.  Node schema capacity therefore
    has to be selected against the budget of this physical request, rather than
    against the configured client default.
    """

    if requested_capacity < 1:
        raise ValueError("requested node schema capacity must be positive")
    rejected: list[int] = []
    for capacity in range(requested_capacity, 0, -1):
        schema = schemas_by_capacity.get(capacity)
        if schema is None:
            rejected.append(capacity)
            continue
        certificate = build_schema_bound_certificate(
            messages=messages,
            schema=schema,
            token_counter=token_counter,
            context_limit=context_limit,
            effective_max_tokens=effective_max_tokens,
            safety_margin_tokens=safety_margin_tokens,
            output_token_counter=output_token_counter,
        )
        if certificate.status == "PASS":
            return SchemaCapacitySelection(
                capacity=capacity,
                certificate=certificate,
                rejected_capacities=tuple(rejected),
            )
        rejected.append(capacity)
    raise StructuredOutputBudgetError(
        "no finite node schema capacity fits the certified context and completion budget"
    )


__all__ = [
    "EdgePageCapacitySelection",
    "RECOVERY_POLICY_REVISION",
    "RECOVERY_POLICY_MAX_CONTEXT_CORRECTIONS",
    "RECOVERY_POLICY_MAX_TRANSIENT_RETRIES",
    "RECOVERY_POLICY_MAX_TRUNCATION_VARIANTS",
    "RUNTIME_RELIABILITY_PROFILE",
    "SCHEMA_REVISION",
    "SchemaBoundCertificate",
    "SchemaCapacitySelection",
    "SchemaBoundednessError",
    "SchemaBoundednessReport",
    "SchemaIssue",
    "StructuredOutputError",
    "StructuredOutputBudgetError",
    "StructuredOutputLengthTruncation",
    "StructuredOutputMalformed",
    "choose_node_schema_capacity",
    "RecoveryAttempt",
    "RecoveryIdentity",
    "StructuredRecoveryController",
    "build_schema_bound_certificate",
    "classify_exception_for_recovery",
    "classify_structured_failure",
    "choose_edge_page_capacity",
    "parse_structured_content",
    "recovery_policy_document",
    "recovery_policy_sha256",
    "reliability_identity",
    "schema_sha256",
    "schema_worst_case_characters",
    "schema_worst_case_json",
    "validate_schema_boundedness",
]
