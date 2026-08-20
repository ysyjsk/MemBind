"""Pure compiler from adapter records to a certified Memory Semantic Graph.

The compiler consumes three explicit layers: an L0 static declaration, L1
operator lineage, and L2 state/effect/publication evidence.  It never infers
dependencies from request order, completion timing, or Python call nesting.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from .effect_journal import (
    EffectCertification,
    EffectScope,
    MemoryEffectJournal,
    MemoryEffectJournalEntry,
    validate_effect_entry,
)
from .publication import (
    PublicationCertification,
    PublicationEvent,
    validate_publication_event,
)
from .semantic_adapter import OperatorLineage, RequestLineage, StaticSemanticContract
from .semantic_contract import (
    EffectContract,
    EffectKind,
    SemanticContract,
    SemanticOperator,
    StateContract,
)
from .semantic_evidence import (
    AdapterProvenance,
    CertificationLevel,
    EffectJournalEntry,
    ExecutionEvidence,
    PublicationEvidence,
)
from .semantic_validator import CertificationStatus, validate_evidence
from .version_token import (
    MemoryVersionToken,
    VersionTokenValidation,
    validate_version_token,
)


class SemanticCompilerError(ValueError):
    """The explicit MEG input cannot be compiled safely."""


def _fail(code: str) -> SemanticCompilerError:
    return SemanticCompilerError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _scope(value: object, code: str) -> frozenset[str] | None:
    if value is None:
        return None
    if not isinstance(value, frozenset):
        raise _fail(code)
    if any(not isinstance(item, str) or not item for item in value):
        raise _fail(code)
    return value


class CompilationStatus(str, Enum):
    CERTIFIED = "CERTIFIED"
    OPAQUE = "OPAQUE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class DependencySpec:
    predecessor_id: str
    successor_id: str
    dependency_type: str

    def __post_init__(self) -> None:
        _text(self.predecessor_id, "dependency_predecessor_invalid")
        _text(self.successor_id, "dependency_successor_invalid")
        _text(self.dependency_type, "dependency_type_invalid")
        if self.dependency_type not in {
            "DATA",
            "DATA_DEP",
            "VERSION",
            "VERSION_DEP",
            "EFFECT_CONFLICT",
            "EFFECT_CONFLICT_DEP",
            "PUBLICATION",
            "PUBLICATION_DEP",
        }:
            raise _fail("dependency_type_unsupported")
        if self.predecessor_id == self.successor_id:
            raise _fail("dependency_self_edge")


@dataclass(frozen=True, slots=True)
class DynamicOperatorEvidence:
    """L1/L2 facts observed by an adapter or persistence wrapper."""

    state_version: MemoryVersionToken | None
    read_scope: frozenset[str] | None
    effect_entry: MemoryEffectJournalEntry | None
    terminal: bool
    child_identity_complete: bool
    hidden_effects_possible: bool
    publication: PublicationEvent | None = None
    provenance: AdapterProvenance | None = None
    semantic_identity: str | None = None
    request_lineage: tuple[RequestLineage, ...] = ()
    dependency_evidence_complete: bool = False

    def __post_init__(self) -> None:
        if self.state_version is not None and not isinstance(
            self.state_version, MemoryVersionToken
        ):
            raise _fail("dynamic_state_version_invalid")
        _scope(self.read_scope, "dynamic_read_scope_invalid")
        if self.effect_entry is not None and not isinstance(
            self.effect_entry, MemoryEffectJournalEntry
        ):
            raise _fail("dynamic_effect_entry_invalid")
        for value, code in (
            (self.terminal, "dynamic_terminal_invalid"),
            (self.child_identity_complete, "dynamic_child_identity_invalid"),
            (self.hidden_effects_possible, "dynamic_hidden_effects_invalid"),
        ):
            if not isinstance(value, bool):
                raise _fail(code)
        if self.publication is not None and not isinstance(self.publication, PublicationEvent):
            raise _fail("dynamic_publication_invalid")
        if self.provenance is not None and not isinstance(self.provenance, AdapterProvenance):
            raise _fail("dynamic_provenance_invalid")
        if self.semantic_identity is not None:
            _text(self.semantic_identity, "dynamic_semantic_identity_invalid")
        if not isinstance(self.request_lineage, tuple) or any(
            not isinstance(item, RequestLineage) for item in self.request_lineage
        ):
            raise _fail("dynamic_request_lineage_invalid")
        request_ids = [item.request_instance_id for item in self.request_lineage]
        request_ordinals = [item.request_ordinal for item in self.request_lineage]
        if len(request_ids) != len(set(request_ids)):
            raise _fail("dynamic_request_lineage_duplicate")
        if request_ordinals != list(range(len(request_ordinals))):
            raise _fail("dynamic_request_lineage_not_canonical")
        if not isinstance(self.dependency_evidence_complete, bool):
            raise _fail("dynamic_dependency_evidence_invalid")


@dataclass(frozen=True, slots=True)
class OperatorInput:
    static_contract: StaticSemanticContract
    lineage: OperatorLineage
    dynamic: DynamicOperatorEvidence
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.static_contract, StaticSemanticContract):
            raise _fail("static_contract_invalid")
        if not isinstance(self.lineage, OperatorLineage):
            raise _fail("lineage_invalid")
        if not isinstance(self.dynamic, DynamicOperatorEvidence):
            raise _fail("dynamic_evidence_invalid")
        if not isinstance(self.evidence_ids, tuple):
            raise _fail("evidence_ids_invalid")
        if len(set(self.evidence_ids)) != len(self.evidence_ids) or any(
            not isinstance(item, str) or not item for item in self.evidence_ids
        ):
            raise _fail("evidence_ids_invalid")
        if self.static_contract.operator_role != self.lineage.semantic_role:
            raise _fail("operator_role_lineage_mismatch")
        if self.static_contract.namespace != self.lineage.graph_id:
            raise _fail("operator_namespace_lineage_mismatch")
        for request in self.dynamic.request_lineage:
            if request.operator_instance_id != self.lineage.instance_id:
                raise _fail("request_operator_lineage_mismatch")
            if (
                self.lineage.coroutine_id is not None
                and request.coroutine_id != self.lineage.coroutine_id
            ):
                raise _fail("request_coroutine_lineage_mismatch")


@dataclass(frozen=True, slots=True)
class CompiledOperator:
    operator: SemanticOperator
    lineage: OperatorLineage
    evidence: ExecutionEvidence
    status: CertificationStatus
    codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledMEG:
    status: CompilationStatus
    operators: tuple[CompiledOperator, ...]
    dependencies: tuple[DependencySpec, ...]
    topological_order: tuple[str, ...]
    codes: tuple[str, ...] = ()


def _default_provenance(item: OperatorInput) -> AdapterProvenance:
    digest = hashlib.sha256(
        f"{item.lineage.adapter_revision}:{item.static_contract.operator_role}".encode("utf-8")
    ).hexdigest()
    source = hashlib.sha256(item.lineage.instance_id.encode("utf-8")).hexdigest()
    return AdapterProvenance(
        adapter_id=f"adapter:{item.lineage.adapter_revision}",
        backend_name="design-fixture",
        backend_version="0",
        contract_id=f"meg.{item.static_contract.operator_role}.v0",
        schema_fingerprint=digest,
        source_fingerprint=source,
        level=CertificationLevel.DECLARED,
    )


def _noop_effect(item: OperatorInput) -> MemoryEffectJournalEntry:
    return MemoryEffectJournalEntry(
        effect_id=f"noop-{item.lineage.instance_id}",
        graph_id=item.lineage.graph_id,
        source_sequence=item.lineage.source_sequence,
        operator_instance_id=item.lineage.instance_id,
        state_version_before=item.dynamic.state_version,
        effect_type=EffectKind.NONE,
        effect_scope=EffectScope.mixed(item.static_contract.namespace),
        mutation_started_ns=item.lineage.created_ns,
        mutation_committed_ns=None,
        mutation_committed=False,
        publication_visible=False,
        state_version_after=None,
        transaction_id=None,
        evidence_hash="0" * 64,
        durable=False,
    )


def _semantic_identity(item: OperatorInput) -> str:
    if item.dynamic.semantic_identity is not None:
        return item.dynamic.semantic_identity
    key = "root" if item.lineage.child_key is None else item.lineage.child_key.canonical_json()
    digest = hashlib.sha256(
        f"{item.static_contract.operator_role}|{key}".encode("utf-8")
    ).hexdigest()
    return f"semantic:{item.static_contract.operator_role}:{digest[:24]}"


def _build_contract(item: OperatorInput) -> SemanticContract:
    static = item.static_contract
    dynamic = item.dynamic
    if static.state_bound:
        state_version = "unknown-state-version"
        if dynamic.state_version is not None:
            state_version = dynamic.state_version.canonical
        state = StateContract.bound(
            namespace=static.namespace,
            version=state_version,
            read_scope=dynamic.read_scope,
        )
    else:
        state = StateContract.unbound(namespace=static.namespace)
    if static.effect_kind is EffectKind.NONE:
        effect = EffectContract.none(namespace=static.namespace)
    else:
        scope = None
        if dynamic.effect_entry is not None and not dynamic.effect_entry.effect_scope.is_unknown:
            scope = dynamic.effect_entry.effect_scope.identifiers
        effect = EffectContract.write(
            namespace=static.namespace,
            kind=static.effect_kind,
            scope=scope,
        )
    return SemanticContract(
        contract_id=f"meg.{static.operator_role}.v0",
        operator_type=static.operator_type,
        state=state,
        effect=effect,
        visibility=static.visibility,
        atomic=static.atomic,
        idempotent=static.idempotent,
        retry_safe=static.retry_safe,
        publication_boundary=static.publication_boundary,
    )


def _legacy_effect_entry(entry: MemoryEffectJournalEntry) -> EffectJournalEntry:
    return EffectJournalEntry(
        effect_id=entry.effect_id,
        operator_instance_id=entry.operator_instance_id,
        kind=entry.effect_type,
        namespace=entry.effect_scope.namespace,
        scope=frozenset(entry.effect_scope.identifiers),
        committed=entry.mutation_committed,
        transaction_id=entry.transaction_id,
        timestamp_ns=entry.mutation_started_ns,
        durable=entry.durable,
    )


def _legacy_publication(event: PublicationEvent) -> PublicationEvidence:
    predecessor = (
        "genesis" if event.predecessor_version is None else event.predecessor_version.canonical
    )
    return PublicationEvidence(
        publication_id=event.event_id,
        operator_instance_id=event.causal_operator_ids[0],
        predecessor_version=predecessor,
        published_version=event.publication_version.canonical,
        durable=event.durable,
        timestamp_ns=event.durable_timestamp_ns,
        frontier_position=event.frontier_position,
    )


class SemanticCompiler:
    """Compile explicit adapter records without scheduling or backend calls."""

    def compile(
        self,
        inputs: tuple[OperatorInput, ...],
        *,
        dependencies: tuple[DependencySpec, ...],
        effect_journal: MemoryEffectJournal | None,
    ) -> CompiledMEG:
        if not isinstance(inputs, tuple):
            raise _fail("operator_inputs_invalid")
        if not isinstance(dependencies, tuple):
            raise _fail("dependencies_invalid")
        if effect_journal is not None and not isinstance(effect_journal, MemoryEffectJournal):
            raise _fail("effect_journal_invalid")
        if not inputs:
            return CompiledMEG(
                status=CompilationStatus.INVALID,
                operators=(),
                dependencies=dependencies,
                topological_order=(),
                codes=("operator_inputs_empty",),
            )
        ids = [item.lineage.instance_id for item in inputs]
        if len(ids) != len(set(ids)):
            raise _fail("operator_instance_duplicate")
        id_set = set(ids)
        predecessors: dict[str, set[str]] = {item_id: set() for item_id in ids}
        dependency_codes: list[str] = []
        if effect_journal is not None and any(
            entry.operator_instance_id not in id_set for entry in effect_journal.entries
        ):
            dependency_codes.append("orphan_effect_journal_entry")
        dependency_keys: set[tuple[str, str, str]] = set()
        for dependency in dependencies:
            if not isinstance(dependency, DependencySpec):
                raise _fail("dependency_invalid")
            if dependency.predecessor_id not in id_set:
                dependency_codes.append("dependency_predecessor_missing")
            if dependency.successor_id not in id_set:
                dependency_codes.append("dependency_successor_missing")
            if dependency.predecessor_id in id_set and dependency.successor_id in id_set:
                predecessors[dependency.successor_id].add(dependency.predecessor_id)
            key = (
                dependency.predecessor_id,
                dependency.successor_id,
                dependency.dependency_type,
            )
            if key in dependency_keys:
                dependency_codes.append("dependency_duplicate")
            dependency_keys.add(key)
        dependency_pairs = {
            (dependency.predecessor_id, dependency.successor_id)
            for dependency in dependencies
        }
        for item in inputs:
            parent_id = item.lineage.parent_operator_instance_id
            if parent_id is None:
                continue
            if parent_id not in id_set:
                dependency_codes.append("lineage_parent_missing")
            elif (parent_id, item.lineage.instance_id) not in dependency_pairs:
                dependency_codes.append("lineage_parent_dependency_missing")
        topo, cycle = _topological(ids, dependencies)
        if cycle:
            dependency_codes.append("dependency_cycle")

        compiled: list[CompiledOperator] = []
        for item in inputs:
            invalid_codes: list[str] = []
            opaque_codes: list[str] = []
            try:
                contract = _build_contract(item)
                operator = SemanticOperator(
                    instance_id=item.lineage.instance_id,
                    semantic_identity=_semantic_identity(item),
                    evidence_ids=item.evidence_ids
                    or (
                        f"l0:{item.static_contract.operator_role}",
                        f"l1:{item.lineage.instance_id}",
                    ),
                    contract=contract,
                    control_predecessors=frozenset(predecessors[item.lineage.instance_id]),
                )
            except ValueError as error:
                compiled.append(
                    CompiledOperator(
                        operator=_placeholder_operator(item),
                        lineage=item.lineage,
                        evidence=_placeholder_evidence(item),
                        status=CertificationStatus.INVALID,
                        codes=(str(error),),
                    )
                )
                continue

            dynamic = item.dynamic
            lineage = item.lineage
            if lineage.ready_ns is None:
                opaque_codes.append("ready_time_missing")
            if dynamic.terminal and lineage.end_ns is None:
                opaque_codes.append("completion_time_missing")
            if lineage.child_key is not None and lineage.coroutine_id is None:
                opaque_codes.append("coroutine_lineage_missing")
            if item.static_contract.resource_class == "llm" and not dynamic.request_lineage:
                opaque_codes.append("request_lineage_missing")
            if item.static_contract.resource_class == "llm" and any(
                request.transport_request_id is None
                for request in dynamic.request_lineage
            ):
                opaque_codes.append("transport_request_lineage_missing")
            if not dynamic.dependency_evidence_complete:
                opaque_codes.append("dependency_provenance_incomplete")
            for request in dynamic.request_lineage:
                if request.created_ns < lineage.created_ns:
                    invalid_codes.append("request_before_operator_creation")
                if lineage.end_ns is not None and request.end_ns > lineage.end_ns:
                    invalid_codes.append("request_after_operator_completion")

            if dynamic.state_version is not None:
                version_result = validate_version_token(dynamic.state_version)
                if version_result.status is VersionTokenValidation.INVALID:
                    invalid_codes.extend(version_result.codes)
                elif version_result.status is VersionTokenValidation.OPAQUE:
                    opaque_codes.extend(version_result.codes)

            effect_entry = dynamic.effect_entry
            if effect_entry is None and item.static_contract.effect_kind is EffectKind.NONE:
                effect_entry = _noop_effect(item)
            if effect_entry is None:
                effect_result = None
                opaque_codes.append("effect_entry_missing")
                legacy_effects: tuple[EffectJournalEntry, ...] = ()
            else:
                effect_result = validate_effect_entry(effect_entry)
                if effect_result.status is EffectCertification.INVALID:
                    invalid_codes.extend(effect_result.codes)
                elif effect_result.status is EffectCertification.OPAQUE:
                    opaque_codes.extend(effect_result.codes)
                legacy_effects = (_legacy_effect_entry(effect_entry),)
                if effect_entry.graph_id != lineage.graph_id:
                    invalid_codes.append("effect_graph_lineage_mismatch")
                if effect_entry.source_sequence != lineage.source_sequence:
                    invalid_codes.append("effect_source_lineage_mismatch")
                if effect_entry.operator_instance_id != lineage.instance_id:
                    invalid_codes.append("effect_operator_lineage_mismatch")
                if item.static_contract.state_bound:
                    if effect_entry.state_version_before is None:
                        opaque_codes.append("effect_before_version_missing")
                    elif effect_entry.state_version_before != dynamic.state_version:
                        invalid_codes.append("effect_before_version_mismatch")
                if (
                    effect_entry.state_version_before is not None
                    and effect_entry.state_version_after is not None
                ):
                    after_result = validate_version_token(
                        effect_entry.state_version_after,
                        predecessor=effect_entry.state_version_before,
                    )
                    if after_result.status is VersionTokenValidation.INVALID:
                        invalid_codes.extend(after_result.codes)
                    elif after_result.status is VersionTokenValidation.OPAQUE:
                        opaque_codes.extend(after_result.codes)

            if dynamic.effect_entry is not None:
                if effect_journal is None:
                    opaque_codes.append("effect_journal_not_supplied")
                else:
                    operator_entries = effect_journal.for_operator(lineage.instance_id)
                    journal_matches = tuple(
                        entry
                        for entry in operator_entries
                        if entry.effect_id == dynamic.effect_entry.effect_id
                    )
                    if not journal_matches:
                        invalid_codes.append("effect_journal_entry_missing")
                    elif len(journal_matches) != 1 or journal_matches[0] != dynamic.effect_entry:
                        invalid_codes.append("effect_journal_entry_mismatch")
                    if len(operator_entries) > 1:
                        opaque_codes.append("effect_journal_aggregation_unknown")
            elif effect_journal is not None and effect_journal.for_operator(
                lineage.instance_id
            ):
                invalid_codes.append("unexpected_effect_journal_entry")

            provenance = item.dynamic.provenance or _default_provenance(item)
            publication = item.dynamic.publication
            if publication is not None:
                if (
                    publication.graph_id != lineage.graph_id
                    or publication.stream_id != lineage.stream_id
                    or publication.source_sequence != lineage.source_sequence
                ):
                    invalid_codes.append("publication_lineage_mismatch")
                if lineage.instance_id not in publication.causal_operator_ids:
                    invalid_codes.append("publication_operator_not_causal")
                if effect_journal is None:
                    opaque_codes.append("publication_journal_not_supplied")
                else:
                    publication_result = validate_publication_event(publication, effect_journal)
                    if publication_result.status is PublicationCertification.INVALID:
                        invalid_codes.extend(publication_result.codes)
                    elif publication_result.status is PublicationCertification.OPAQUE:
                        opaque_codes.extend(publication_result.codes)
            legacy_publication = _legacy_publication(publication) if publication is not None else None
            evidence = ExecutionEvidence(
                instance_id=item.lineage.instance_id,
                semantic_identity=_semantic_identity(item),
                state_version=(
                    None
                    if item.dynamic.state_version is None
                    else item.dynamic.state_version.canonical
                ),
                read_scope=item.dynamic.read_scope,
                provenance=provenance,
                effect_journal=legacy_effects,
                publication=legacy_publication,
                terminal=item.dynamic.terminal,
                child_identity_complete=item.dynamic.child_identity_complete,
                hidden_effects_possible=item.dynamic.hidden_effects_possible,
            )
            result = validate_evidence(operator, evidence)
            if result.status is CertificationStatus.INVALID:
                invalid_codes.extend(result.codes)
            elif result.status is CertificationStatus.OPAQUE:
                opaque_codes.extend(result.codes)
            if invalid_codes:
                status = CertificationStatus.INVALID
            elif opaque_codes:
                status = CertificationStatus.OPAQUE
            else:
                status = result.status
            compiled.append(
                CompiledOperator(
                    operator=operator,
                    lineage=item.lineage,
                    evidence=evidence,
                    status=status,
                    codes=tuple(dict.fromkeys((*invalid_codes, *opaque_codes))),
                )
            )

        statuses = [item.status for item in compiled]
        if CertificationStatus.INVALID in statuses or dependency_codes:
            status = CompilationStatus.INVALID
        elif CertificationStatus.OPAQUE in statuses:
            status = CompilationStatus.OPAQUE
        else:
            status = CompilationStatus.CERTIFIED
        return CompiledMEG(
            status=status,
            operators=tuple(compiled),
            dependencies=dependencies,
            topological_order=topo,
            codes=tuple(dict.fromkeys(dependency_codes)),
        )


def _topological(
    ids: list[str], dependencies: tuple[DependencySpec, ...]
) -> tuple[tuple[str, ...], bool]:
    indegree = {item: 0 for item in ids}
    successors: dict[str, list[str]] = {item: [] for item in ids}
    for dependency in dependencies:
        if dependency.predecessor_id not in indegree or dependency.successor_id not in indegree:
            continue
        indegree[dependency.successor_id] += 1
        successors[dependency.predecessor_id].append(dependency.successor_id)
    ready = sorted(item for item, count in indegree.items() if count == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for successor in sorted(successors[current]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
                ready.sort()
    return tuple(order), len(order) != len(ids)


def _placeholder_operator(item: OperatorInput) -> SemanticOperator:
    """Keep result shape stable after malformed input while staying invalid."""

    from .semantic_contract import OperatorType, Visibility

    contract = SemanticContract(
        contract_id="invalid.placeholder",
        operator_type=OperatorType.RETRIEVAL,
        state=StateContract.unbound(namespace=item.static_contract.namespace),
        effect=EffectContract.none(namespace=item.static_contract.namespace),
        visibility=Visibility.PRIVATE_INTERMEDIATE,
        atomic=True,
        idempotent=True,
        retry_safe=False,
        publication_boundary=False,
    )
    return SemanticOperator(
        instance_id=item.lineage.instance_id,
        semantic_identity="invalid-placeholder",
        evidence_ids=(),
        contract=contract,
        control_predecessors=frozenset(),
    )


def _placeholder_evidence(item: OperatorInput) -> ExecutionEvidence:
    return ExecutionEvidence(
        instance_id=item.lineage.instance_id,
        semantic_identity=None,
        state_version=None,
        read_scope=None,
        provenance=None,
        effect_journal=(),
        publication=None,
        terminal=False,
        child_identity_complete=False,
        hidden_effects_possible=True,
    )


__all__ = [
    "CompilationStatus",
    "CompiledMEG",
    "CompiledOperator",
    "DependencySpec",
    "DynamicOperatorEvidence",
    "OperatorInput",
    "SemanticCompiler",
    "SemanticCompilerError",
]
