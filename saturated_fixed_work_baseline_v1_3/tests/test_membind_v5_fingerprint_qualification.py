from __future__ import annotations

import hashlib
import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v5.fingerprint_qualification import (
    PASS_GATE,
    qualify_fingerprint_noninterference,
    write_fingerprint_qualification_artifacts,
)


def test_provider_free_noninterference_passes_without_provider_imports() -> None:
    result = qualify_fingerprint_noninterference()
    assert result["decision"]["gate"] == PASS_GATE
    assert result["live_execution"] is False
    assert result["provider_free"] is True
    assert all(result["checks"].values())
    assert result["decision"]["source_0_diagnostic_may_be_considered"] is True


def test_qualification_writer_is_fresh_and_deterministic(tmp_path: Path) -> None:
    result = qualify_fingerprint_noninterference()
    paths = write_fingerprint_qualification_artifacts(result, tmp_path / "qualification")
    assert {path.name for path in paths} == {
        "SFWB_V13_V5_FINGERPRINT_QUALIFICATION.json",
        "SFWB_V13_V5_FINGERPRINT_NONINTERFERENCE.md",
    }
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["decision"]["gate"] == PASS_GATE
    before = hashlib.sha256(paths[0].read_bytes()).hexdigest()
    try:
        write_fingerprint_qualification_artifacts(result, tmp_path / "qualification")
    except ValueError as error:
        assert str(error) == "FINGERPRINT_QUALIFICATION_ROOT_ALREADY_EXISTS"
    else:
        raise AssertionError("qualification writer must not overwrite an existing root")
    assert hashlib.sha256(paths[0].read_bytes()).hexdigest() == before
