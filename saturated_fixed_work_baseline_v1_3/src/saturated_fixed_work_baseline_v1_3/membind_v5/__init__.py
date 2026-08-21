"""Offline V5 evidence analysis for SFWB v1.3.

The package intentionally has no Graphiti, Neo4j, model, or runtime imports.
It reads validated sealed artifacts and produces reproducible research reports.
"""

from .offline_analyzer import (
    ANALYSIS_ROOT_NAME,
    EXPECTED_BLOCKS,
    analyze_sealed_workload,
    write_analysis_artifacts,
)
from .first_divergence import (
    FIRST_DIVERGENCE_ROOT_NAME,
    STOP_GATE,
    analyze_first_divergence,
    write_first_semantic_divergence_artifacts,
    write_first_divergence_artifacts,
)
from .semantic_fingerprint import (
    FINGERPRINT_SCHEMA_VERSION,
    SemanticFingerprintError,
    fingerprint_records,
    semantic_fingerprint,
    semantic_projection,
)
from .fingerprint_qualification import (
    FAIL_GATE as FINGERPRINT_QUALIFICATION_FAIL_GATE,
    PASS_GATE as FINGERPRINT_QUALIFICATION_PASS_GATE,
    QUALIFICATION_ROOT_NAME,
    qualify_fingerprint_noninterference,
    write_fingerprint_qualification_artifacts,
)

__all__ = [
    "ANALYSIS_ROOT_NAME",
    "EXPECTED_BLOCKS",
    "analyze_sealed_workload",
    "write_analysis_artifacts",
    "FIRST_DIVERGENCE_ROOT_NAME",
    "STOP_GATE",
    "analyze_first_divergence",
    "write_first_divergence_artifacts",
    "write_first_semantic_divergence_artifacts",
    "FINGERPRINT_SCHEMA_VERSION",
    "SemanticFingerprintError",
    "fingerprint_records",
    "semantic_fingerprint",
    "semantic_projection",
    "FINGERPRINT_QUALIFICATION_FAIL_GATE",
    "FINGERPRINT_QUALIFICATION_PASS_GATE",
    "QUALIFICATION_ROOT_NAME",
    "qualify_fingerprint_noninterference",
    "write_fingerprint_qualification_artifacts",
]
