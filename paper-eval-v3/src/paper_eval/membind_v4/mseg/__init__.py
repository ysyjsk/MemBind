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
]
