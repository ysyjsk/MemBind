from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/cleanup_membind_v31_namespace.py"


def _module():
    spec = importlib.util.spec_from_file_location("membind_v31_cleanup", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_failed_attempt_requires_sealed_nonreusable_manifest() -> None:
    module = _module()
    body = {
        "schema_version": "membind.paper-eval-v3.membind-v31-single-history-feasibility.v1",
        "status": "FAILED_NON_REUSABLE",
        "attempt_id": "membind-v31-feasibility-20260818-003",
        "namespace": "pev3-membind-v31-dev-20260818-002-membind-07741c45",
    }
    with pytest.raises(module.CleanupError, match="manifest_hash"):
        module.validate_failed_attempt(
            body,
            expected_attempt_id=body["attempt_id"],
            expected_namespace=body["namespace"],
        )


def test_build_cleanup_evidence_is_exact_and_sealed() -> None:
    module = _module()
    evidence = module.build_cleanup_evidence(
        attempt_id="membind-v31-feasibility-20260818-003",
        namespace="pev3-membind-v31-dev-20260818-002-membind-07741c45",
        pre_state={"node_count": 177, "relationship_count": 355},
        post_state={"node_count": 0, "relationship_count": 0},
    )
    assert evidence["scope"] == "EXACT_GROUP_ID_ONLY"
    assert evidence["global_cleanup_used"] is False
    assert evidence["post_cleanup_node_count"] == 0
    assert evidence["post_cleanup_relationship_count"] == 0
    assert evidence["payload_sha256"]


def test_build_cleanup_evidence_rejects_nonempty_post_state() -> None:
    module = _module()
    with pytest.raises(module.CleanupError, match="post_cleanup_nonzero"):
        module.build_cleanup_evidence(
            attempt_id="membind-v31-feasibility-20260818-003",
            namespace="pev3-membind-v31-dev-20260818-002-membind-07741c45",
            pre_state={"node_count": 1, "relationship_count": 1},
            post_state={"node_count": 1, "relationship_count": 0},
        )


def test_cleanup_driver_path_passes_only_the_exact_group_id() -> None:
    module = _module()

    class Driver:
        empty = False

        async def execute_query(self, *_args, **_kwargs):
            count = 0 if self.empty else 4
            return [{"node_count": count, "relationship_count": count, "episode_names": []}]

    driver = Driver()
    calls: list[list[str] | None] = []

    async def clear_data(selected_driver, group_ids=None):
        assert selected_driver is driver
        calls.append(group_ids)
        driver.empty = True

    before, after = asyncio.run(
        module.cleanup_exact_namespace_with_driver(
            driver=driver,
            namespace="pev3-membind-v31-dev-test-membind-07741c45",
            clear_data_fn=clear_data,
        )
    )
    assert calls == [["pev3-membind-v31-dev-test-membind-07741c45"]]
    assert before["node_count"] == 4
    assert after["node_count"] == 0
