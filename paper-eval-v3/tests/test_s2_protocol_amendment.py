from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AMENDMENT = ROOT / "PAPER_EVALUATION_PROTOCOL_AMENDMENT_v1.1.md"
AUDIT = ROOT / "S2_LITERATURE_AND_CODE_DESIGN_AUDIT_20260814.md"


def test_s2_amendment_preserves_parent_and_freezes_exact_next_scope() -> None:
    text = AMENDMENT.read_text(encoding="utf-8")
    assert "4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e" in text
    assert "S2-R0" in text
    assert "S3 remains unauthorized" in text
    assert "Graphiti 0.29.3" in text
    assert "does not select the final paper retrieval policy" in text
    assert "EntityNode, CommunityNode, and multi-surface retrieval" in text
    assert "direct violations" in text


def test_literature_audit_pins_official_sources_and_limits_claims() -> None:
    text = AUDIT.read_text(encoding="utf-8")
    for commit in (
        "4552fed19bc0cde7b990a6ceb0365cd75b1b3453",
        "a844d993f77f947f682a0a52ec2825f2950bc0b3",
        "6d279a5f5d40ee229e1995df15c182cb2062c71c",
        "9e0b455f4ef0e2ab8f2e582289761153549043fc",
    ):
        assert commit in text
    assert "Entity-only versus Edge-only" in text
    assert "fractional evidence-session coverage" in text
    assert "does not report LongMemEval Session Recall@k" in text
    assert "contextual evidence, not authority" in text


def test_protocol_documents_do_not_contain_private_runtime_secrets() -> None:
    combined = (AMENDMENT.read_text(encoding="utf-8") + AUDIT.read_text(encoding="utf-8")).lower()
    for forbidden in ("api-key", "api_key", "10.87.5.247", "127.0.0.1:17897"):
        assert forbidden not in combined
