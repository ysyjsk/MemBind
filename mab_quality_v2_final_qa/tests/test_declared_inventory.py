from __future__ import annotations

import json
from pathlib import Path

import pytest

from mab_quality_v2_final_qa.qualification import qualify_declared_inventory


DATASET = Path("/tmp/mab_longmemeval_5.json")
REVISION = "hf:ai-hyz/MemoryAgentBench@7ea066982b140a19337e17e60d45d4076e042faf"


@pytest.mark.skipif(not DATASET.is_file(), reason="official pinned fixture unavailable")
def test_four_context_inventory_is_frozen_before_quality_execution() -> None:
    records = json.loads(DATASET.read_text(encoding="utf-8"))
    result = qualify_declared_inventory(
        records,
        source="longmemeval_s*",
        dataset_revision=REVISION,
        included_record_indices=(0, 1, 2, 3),
        excluded_failures={
            4: "question 38 gold session is absent from common context"
        },
    )

    assert result["decision"] == "PASS_DECLARED_INVENTORY_QUALIFIED"
    assert result["live_authorized"] is True
    assert result["included_context_count"] == 4
    assert result["included_qa_count"] == 240
    assert result["excluded_context_count"] == 1
    assert len(result["question_inventory_sha256"]) == 64


@pytest.mark.skipif(not DATASET.is_file(), reason="official pinned fixture unavailable")
def test_declared_inventory_rejects_unexplained_or_valid_exclusion() -> None:
    records = json.loads(DATASET.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="DECLARED_INVENTORY_PARTITION_INVALID"):
        qualify_declared_inventory(
            records,
            source="longmemeval_s*",
            dataset_revision=REVISION,
            included_record_indices=(0, 1, 2, 3),
            excluded_failures={},
        )
    with pytest.raises(ValueError, match="DECLARED_EXCLUSION_NOT_REPRODUCED"):
        qualify_declared_inventory(
            records,
            source="longmemeval_s*",
            dataset_revision=REVISION,
            included_record_indices=(0, 2, 3),
            excluded_failures={
                1: "invented",
                4: "question 38 gold session is absent from common context",
            },
        )
