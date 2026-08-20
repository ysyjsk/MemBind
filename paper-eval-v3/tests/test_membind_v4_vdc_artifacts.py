"""TDD contracts for VDC capture bundles and oracle row materialization."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from paper_eval.membind_v4.vdc.artifacts import (
    VDCArtifactError,
    build_vdc_oracle_rows,
    read_publication_times,
    write_vdc_bundle,
)
from paper_eval.membind_v4.vdc.live_composition import VDCObservationBundle
from paper_eval.membind_v4.vdc.oracle import reduce_vdc_oracle

from test_membind_v4_vdc_capture_replay import _capture


def test_bundle_writer_is_hashable_and_refuses_duplicate_capture(tmp_path: Path) -> None:
    bundle = VDCObservationBundle()
    bundle.record_capture(_capture())
    path = tmp_path / "VDC_CAPTURE_BUNDLE.json"

    document = write_vdc_bundle(path, bundle)

    assert path.is_file()
    assert document["schema_version"].endswith("capture-bundle.v1")
    assert document["capture_count"] == 1
    assert document["payload_sha256"]
    with pytest.raises(VDCArtifactError, match="bundle_output_exists"):
        write_vdc_bundle(path, bundle)


def test_publication_reader_verifies_event_hash(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"event":{"event_sequence":0,"event_type":"PUBLICATION_DURABLE",'
        '"source_sequence":0,"timestamp_ns":100},"event_sha256":"bad"}\n',
        encoding="utf-8",
    )

    with pytest.raises(VDCArtifactError, match="lifecycle_event_hash_mismatch"):
        read_publication_times(path)


def test_oracle_rows_require_all_adjacent_capture_evidence(tmp_path: Path) -> None:
    bundle = VDCObservationBundle()
    bundle.record_capture(_capture())

    with pytest.raises(VDCArtifactError, match="exact_read_missing"):
        build_vdc_oracle_rows(
            bundle,
            publication_times={0: 100},
            expected_source_sequences=(1,),
        )

