"""MemBind v4 implementation lane.

The package is intentionally independent from the frozen v3.1 runtime.  P0
contains only read-only evidence binding and deterministic prefix reduction;
P1 exposes the exact semantic-call and NodeResolve adapter boundaries.
"""

from .node_resolve_adapter import (
    ExactNodeResolveResult,
    NodeResolveAdapter,
    NodeResolveAdapterError,
    NodeResolveV4Adapter,
    NodeResolveV4AdapterProtocol,
    PreparedNodeResolveCall,
    PreparedSemanticCall,
    assert_serial_factorized_parity,
)
from .semantic_call import (
    SemanticCall,
    SemanticCallDecision,
    SemanticCallError,
    semantic_call_fingerprint,
    validate_semantic_call_pair,
)
from .admission import (
    AdmissionDecision,
    AdmissionRequest,
    RequestKind,
    ResourceGatedAdmission,
    ResourceGatedAdmissionController,
    V4AdmissionError,
)
from .coordinator import V4Coordinator, V4CoordinatorError, run_membind_v4_stream, run_v4_stream
from .resource_profile import (
    Criticality,
    RequestProfile,
    ResourceClass,
    ResourceProfileError,
    classify_request_profile,
)
from .runtime import (
    NodeResolveOutcome,
    NodeResolvePrepared,
    PreparedNodeResolve,
    V4NodeResolveRuntime,
    V4RuntimeError,
    ValidatedNodeResolveRuntime,
    ValidatedSpeculationRuntime,
)
from .telemetry import V4Telemetry, V4TelemetryError
from .live_adapter import (
    V4LiveNodeResolveBridge,
    V4LiveNodeResolveError,
    build_v31_graphiti_v4_bridge,
    graphiti_node_resolve_capability,
)
from .graphiti_factorization import (
    CapturedGraphitiRequest,
    V4GraphitiFactorizedAdapter,
    V4GraphitiFactorizationError,
    v4_node_resolve_callbacks,
)
from .live_preflight import (
    PREFLIGHT_READY,
    PREFLIGHT_REMOTE_UNAVAILABLE,
    PREFLIGHT_SANDBOX_NETWORK_ISOLATION,
    build_preflight_artifact,
    classify_socket_error,
    probe_services,
    read_env_file,
)
from .reducer import (
    V4_FINAL_OUTPUT_FILES,
    V4ReducerError,
    reduce_candidate,
    reduce_v4_final,
    write_v4_final_outputs,
)
from .runner import V4RunnerError, run_candidate
from .production_runner import (
    CANDIDATE_HISTORY_ID,
    CANDIDATE_SOURCE_COUNTS,
    V4ProductionRunnerError,
    build_v4_candidate_live_runner,
    build_v4_candidate_plan,
    verify_prior_six_reduction,
)
from .live_runner import V4LiveRunnerError, V4PreparedSource, run_v4_live_prepared_stream
from .live_block import (
    V4LiveBlockComposition,
    V4LiveBlockError,
    V4ProductionLoaders,
    build_v4_live_composition,
    build_v4_live_block_hooks,
    build_v4_live_hooks,
    build_v4_production_block_runner,
    build_v4_full_history_runner,
    build_v4_full_run_history_runner,
    execute_v4_live_block,
    production_v4_live_hooks,
    production_v4_loaders,
)
from .freeze import FORMAL_HISTORY_IDS, V4FreezeError, build_frozen_method, verify_frozen_method
from .full_run import FORMAL_HISTORY_SOURCE_COUNTS, V4FullRunError, run_v4_full

__all__ = [
    "ExactNodeResolveResult",
    "NodeResolveAdapter",
    "NodeResolveAdapterError",
    "NodeResolveV4Adapter",
    "NodeResolveV4AdapterProtocol",
    "PreparedNodeResolveCall",
    "PreparedSemanticCall",
    "SemanticCall",
    "SemanticCallDecision",
    "SemanticCallError",
    "assert_serial_factorized_parity",
    "semantic_call_fingerprint",
    "validate_semantic_call_pair",
    "AdmissionDecision",
    "AdmissionRequest",
    "Criticality",
    "NodeResolveOutcome",
    "NodeResolvePrepared",
    "PreparedNodeResolve",
    "RequestKind",
    "RequestProfile",
    "ResourceClass",
    "ResourceGatedAdmission",
    "ResourceGatedAdmissionController",
    "ResourceProfileError",
    "V4AdmissionError",
    "V4CoordinatorError",
    "V4Coordinator",
    "V4NodeResolveRuntime",
    "V4RuntimeError",
    "V4Telemetry",
    "V4TelemetryError",
    "V4LiveNodeResolveBridge",
    "V4LiveNodeResolveError",
    "build_v31_graphiti_v4_bridge",
    "graphiti_node_resolve_capability",
    "CapturedGraphitiRequest",
    "V4GraphitiFactorizedAdapter",
    "V4GraphitiFactorizationError",
    "v4_node_resolve_callbacks",
    "V4ReducerError",
    "V4_FINAL_OUTPUT_FILES",
    "V4RunnerError",
    "PREFLIGHT_READY",
    "PREFLIGHT_REMOTE_UNAVAILABLE",
    "PREFLIGHT_SANDBOX_NETWORK_ISOLATION",
    "build_preflight_artifact",
    "classify_socket_error",
    "probe_services",
    "read_env_file",
    "reduce_candidate",
    "reduce_v4_final",
    "write_v4_final_outputs",
    "run_candidate",
    "CANDIDATE_HISTORY_ID",
    "CANDIDATE_SOURCE_COUNTS",
    "V4ProductionRunnerError",
    "build_v4_candidate_live_runner",
    "build_v4_candidate_plan",
    "verify_prior_six_reduction",
    "V4LiveRunnerError",
    "V4PreparedSource",
    "FORMAL_HISTORY_IDS",
    "V4FreezeError",
    "V4FullRunError",
    "FORMAL_HISTORY_SOURCE_COUNTS",
    "ValidatedSpeculationRuntime",
    "ValidatedNodeResolveRuntime",
    "classify_request_profile",
    "run_membind_v4_stream",
    "run_v4_stream",
    "run_v4_live_prepared_stream",
    "V4LiveBlockComposition",
    "V4LiveBlockError",
    "V4ProductionLoaders",
    "build_v4_live_composition",
    "build_v4_live_block_hooks",
    "build_v4_live_hooks",
    "build_v4_production_block_runner",
    "build_v4_full_history_runner",
    "build_v4_full_run_history_runner",
    "execute_v4_live_block",
    "production_v4_live_hooks",
    "production_v4_loaders",
    "build_frozen_method",
    "verify_frozen_method",
    "run_v4_full",
]
