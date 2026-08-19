"""Independent Multi-QA quality analysis lane.

The package is intentionally not installed into the existing ``paper_eval``
namespace. It can be imported by adding this directory's ``src`` to
``PYTHONPATH`` and treats the existing Quality-v1 implementation as read-only.
"""

from .compatibility import QualityV1CompatibilityError, to_quality_v1_record
from .contracts import (
    FAILURE_TAXONOMY,
    MABQA,
    MABContext,
    MABSession,
    PrivateQALabels,
    PublicContext,
    assert_gold_blind,
    canonical_sha256,
)
from .dataset_adapter import DatasetMappingError, MABDatasetAdapter
from .reducer import reduce_method_rows, reduce_paired_rows
from .runner import (
    MABQualityRunner,
    NamespaceNotSealedError,
    QAResult,
    QAWriteViolation,
)

__all__ = [
    "FAILURE_TAXONOMY",
    "MABQA",
    "DatasetMappingError",
    "MABContext",
    "MABDatasetAdapter",
    "MABQualityRunner",
    "MABSession",
    "NamespaceNotSealedError",
    "PrivateQALabels",
    "PublicContext",
    "QAResult",
    "QAWriteViolation",
    "QualityV1CompatibilityError",
    "assert_gold_blind",
    "canonical_sha256",
    "reduce_method_rows",
    "reduce_paired_rows",
    "to_quality_v1_record",
]
