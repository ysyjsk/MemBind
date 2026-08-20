"""Offline request-level publication scheduling oracle.

This package is intentionally independent from the frozen MemBind v4 runtime.
It consumes sealed traces, reconstructs only evidence-backed request
dependencies, and never creates or executes an LLM request.
"""

from .model import (
    NOT_OBSERVABLE,
    DependencyKind,
    DAGEdge,
    PublicationRecord,
    RequestRecord,
    ReplayResult,
    TraceBundle,
)
from .artifacts import analyze_bundle, write_analysis_artifacts
from .request_dag import RequestDAG, build_request_dag
from .replay import ReplayError, replay
from .trace_parser import TraceParseError, load_trace_bundle

__all__ = [
    "NOT_OBSERVABLE",
    "DependencyKind",
    "DAGEdge",
    "PublicationRecord",
    "RequestRecord",
    "ReplayError",
    "ReplayResult",
    "RequestDAG",
    "TraceBundle",
    "TraceParseError",
    "build_request_dag",
    "analyze_bundle",
    "load_trace_bundle",
    "replay",
    "write_analysis_artifacts",
]
