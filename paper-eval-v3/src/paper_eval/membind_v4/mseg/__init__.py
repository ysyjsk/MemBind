"""Conservative offline contracts for Memory Semantic Execution Graphs.

This package is intentionally independent from the frozen v3.1 runtime.  It
models evidence and offline oracles; importing it installs no scheduler,
transport wrapper, database hook, or speculative execution path.
"""

from .conflict import ConflictClass, MemoryScope, classify_operator_conflict
from .critical_path import (
    CriticalPathEntry,
    PublicationCriticalPath,
    analyze_publication_critical_path,
)
from .dependency import (
    DependencyEdge,
    DependencyKnowledgeState,
    DependencyType,
    MemorySemanticExecutionGraph,
    OperatorInstance,
)
from .operator_identity import OperatorIdentity, OperatorIdentityError
from .observability import (
    MSEGObservabilityError,
    MSEGOperatorContext,
    MSEGOperatorTraceObserver,
    MSEGWorkflowContext,
    current_operator_context,
    current_operator_metadata,
    current_trace_observer,
    current_workflow_context,
    trace_observer_scope,
    workflow_scope,
)
from .instrumented_adapter import (
    MSEGInstrumentedAdapter,
    MSEGInstrumentedAdapterError,
    instrument_graphiti_semantic_binding,
)
from .q0_reducer import Q0QualificationError, reduce_q0_qualification
from .qualification import Q0CompositionError, Q0LiveComposition, build_q0_live_composition
from .oracle import OracleError, OracleSchedule, ScheduleRecord, schedule_finite_resource
from .reducer import MSEGReducerError, audit_llm_trace_observability

__all__ = [
    "ConflictClass",
    "CriticalPathEntry",
    "DependencyEdge",
    "DependencyKnowledgeState",
    "DependencyType",
    "MSEGReducerError",
    "MemoryScope",
    "MemorySemanticExecutionGraph",
    "MSEGObservabilityError",
    "MSEGOperatorContext",
    "MSEGOperatorTraceObserver",
    "MSEGWorkflowContext",
    "MSEGInstrumentedAdapter",
    "MSEGInstrumentedAdapterError",
    "OperatorIdentity",
    "OperatorIdentityError",
    "OperatorInstance",
    "OracleError",
    "OracleSchedule",
    "PublicationCriticalPath",
    "ScheduleRecord",
    "analyze_publication_critical_path",
    "audit_llm_trace_observability",
    "classify_operator_conflict",
    "schedule_finite_resource",
    "current_operator_context",
    "current_operator_metadata",
    "current_trace_observer",
    "current_workflow_context",
    "trace_observer_scope",
    "workflow_scope",
    "instrument_graphiti_semantic_binding",
    "Q0QualificationError",
    "reduce_q0_qualification",
    "Q0CompositionError",
    "Q0LiveComposition",
    "build_q0_live_composition",
]
