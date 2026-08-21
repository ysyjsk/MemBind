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

__all__ = [
    "ANALYSIS_ROOT_NAME",
    "EXPECTED_BLOCKS",
    "analyze_sealed_workload",
    "write_analysis_artifacts",
]
