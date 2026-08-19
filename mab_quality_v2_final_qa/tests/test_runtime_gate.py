from __future__ import annotations

from mab_quality_v2_final_qa.runtime_gate import check_model_port


def test_unavailable_model_port_is_reported_without_live_call() -> None:
    status = check_model_port(8002, timeout=0.1)
    assert status.available is False
    assert status.port == 8002
