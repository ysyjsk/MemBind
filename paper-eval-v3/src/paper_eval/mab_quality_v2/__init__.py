"""Isolated MemoryAgentBench multi-QA quality lane.

The package is deliberately an orchestration layer.  Existing Quality-v1
retrieval, context and Reader implementations remain read-only dependencies.
"""

from .contracts import (
    FAILURE_TAXONOMY,
    MABContext,
    MABQA,
    MABSession,
    PublicContext,
    PrivateQALabels,
)
from .dataset_adapter import MABDatasetAdapter
from .reducer import reduce_method_rows, reduce_paired_rows

__all__ = [
    "FAILURE_TAXONOMY",
    "MABContext",
    "MABDatasetAdapter",
    "MABQA",
    "MABSession",
    "PrivateQALabels",
    "PublicContext",
    "reduce_method_rows",
    "reduce_paired_rows",
]
