"""Offline tests for the v4 service preflight and failure classification."""

from __future__ import annotations

import errno
import json
from pathlib import Path

from paper_eval.membind_v4.live_preflight import (
    PREFLIGHT_SANDBOX_NETWORK_ISOLATION,
    build_preflight_artifact,
    classify_socket_error,
    read_env_file,
)
import paper_eval.membind_v4.live_preflight as preflight


def test_socket_permission_failure_is_sandbox_classification() -> None:
    assert classify_socket_error(PermissionError(errno.EPERM, "operation not permitted")) == (
        PREFLIGHT_SANDBOX_NETWORK_ISOLATION
    )


def test_preflight_artifact_is_content_safe_and_sealed() -> None:
    artifact = build_preflight_artifact(
        construction={"status": "BLOCKED", "endpoint": "http://10.0.0.1:8000/v1/models"},
        embedding={"status": "BLOCKED", "endpoint": "http://10.0.0.1:8001/v1/models"},
        neo4j={"status": "BLOCKED", "endpoint": "bolt://127.0.0.1:7687"},
        classification=PREFLIGHT_SANDBOX_NETWORK_ISOLATION,
    )
    assert artifact["status"] == "BLOCKED_SERVICE_PREflight".upper()
    assert artifact["classification"] == PREFLIGHT_SANDBOX_NETWORK_ISOLATION
    assert "payload_sha256" in artifact
    encoded = json.dumps(artifact)
    assert "password" not in encoded.lower()
    assert "api_key" not in encoded.lower()


def test_env_reader_does_not_expand_or_leak_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "CONSTRUCTION_LLM_BASE_URL=http://example.invalid/v1/\n"
        "IGNORED=secret\n"
        "# comment\n",
        encoding="utf-8",
    )
    values = read_env_file(path)
    assert values == {"CONSTRUCTION_LLM_BASE_URL": "http://example.invalid/v1/"}


def test_default_route_is_usable_but_empty_route_table_is_isolation(monkeypatch) -> None:
    monkeypatch.setattr(
        preflight.Path,
        "read_text",
        lambda _self, **_kwargs: (
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
            "eth0\t00000000\t0102A8C0\t0003\t0\t0\t0\t00000000\t1500\t0\t0\n"
        ),
    )
    assert preflight._route_available() is True

    monkeypatch.setattr(preflight.Path, "read_text", lambda _self, **_kwargs: "header\n")
    assert preflight._route_available() is False
