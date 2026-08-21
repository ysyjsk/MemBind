from __future__ import annotations

from pathlib import Path

from paper_eval.membind_v4.mseg.metadata_audit import (
    audit_metadata_noninterference,
    render_metadata_noninterference_audit,
)


def test_metadata_noninterference_audit_is_provider_free_and_passes() -> None:
    root = Path(__file__).resolve().parents[1]
    audit = audit_metadata_noninterference(root)
    assert audit["status"] == "PASS"
    assert all(audit["checks"].values())
    document = render_metadata_noninterference_audit(audit)
    assert "production semantic data plane" in document
    assert "observability metadata plane" in document
    assert "OPAQUE" in document
