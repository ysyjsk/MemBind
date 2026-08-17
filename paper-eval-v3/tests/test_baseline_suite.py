"""RED contracts for the isolated three-baseline development suite.

These tests are deliberately service-free.  They freeze orchestration,
identity, and fail-closed restart semantics before a suite runner is allowed
to contact Graphiti, vLLM, the embedding service, or Neo4j.
"""

from __future__ import annotations

import importlib
import re
from copy import deepcopy

import pytest


HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
METHODS = ("U0", "A0", "P(C=2)")


def _sut():
    # Kept inside the tests so the initial missing-module state is reported as
    # ordinary RED test cases rather than aborting collection.
    return importlib.import_module("paper_eval.baseline_suite")


def _plan(*, mode: str = "development", attempt_ordinal: int = 1):
    return _sut().build_baseline_suite_plan(
        "bs-20260816-001",
        mode=mode,
        attempt_ordinal=attempt_ordinal,
    )


def test_registry_contains_exactly_three_baselines_in_execution_order() -> None:
    sut = _sut()

    assert sut.BASELINE_METHODS == METHODS
    assert sut.DEVELOPMENT_HISTORIES == HISTORIES
    for method in METHODS:
        assert sut.canonicalize_baseline_method(method) == method
    with pytest.raises(sut.BaselineSuiteError, match="method|baseline"):
        sut.canonicalize_baseline_method("M*")


def test_canary_is_exactly_one_u0_one_a0_and_two_parallel_episodes() -> None:
    plan = _plan(mode="canary")

    assert plan["mode"] == "canary"
    assert [block["method"] for block in plan["blocks"]] == list(METHODS)
    assert [block["history_id"] for block in plan["blocks"]] == [
        HISTORIES[0],
        HISTORIES[0],
        HISTORIES[0],
    ]
    assert [block["episode_limit"] for block in plan["blocks"]] == [1, 1, 2]
    assert [block["block_index"] for block in plan["blocks"]] == [0, 1, 2]
    assert _sut().verify_baseline_suite_plan(plan) == plan


def test_development_plan_is_method_major_over_the_exact_four_histories() -> None:
    plan = _plan()
    blocks = plan["blocks"]

    assert plan["mode"] == "development"
    assert len(blocks) == 12
    assert [(block["method"], block["history_id"]) for block in blocks] == [
        (method, history_id)
        for method in METHODS
        for history_id in HISTORIES
    ]
    assert [block["block_index"] for block in blocks] == list(range(12))
    assert all(block["episode_limit"] is None for block in blocks)
    assert _sut().verify_baseline_suite_plan(plan) == plan


@pytest.mark.parametrize(
    "mutation",
    [
        lambda plan: plan["methods"].append("M*"),
        lambda plan: plan["histories"].reverse(),
        lambda plan: plan["blocks"][0].update(method="M*"),
        lambda plan: plan["blocks"][0].update(history_id="PILOT_PRIVATE"),
        lambda plan: plan["blocks"][0].update(block_index=9),
        lambda plan: plan["blocks"].reverse(),
        lambda plan: plan["blocks"][0].update(namespace="foreign"),
    ],
)
def test_plan_verifier_recomputes_inventory_and_rejects_drift(mutation) -> None:
    sut = _sut()
    plan = _plan()
    mutation(plan)

    with pytest.raises(sut.BaselineSuiteError):
        sut.verify_baseline_suite_plan(plan)


def test_namespaces_are_deterministic_unique_and_attempt_scoped() -> None:
    first = _plan()
    repeated = _plan()
    retry = _plan(attempt_ordinal=2)
    namespaces = [block["namespace"] for block in first["blocks"]]

    assert namespaces == [block["namespace"] for block in repeated["blocks"]]
    assert len(namespaces) == len(set(namespaces)) == 12
    assert all(
        re.fullmatch(
            r"pev3-bs-20260816-001-(?:u0|a0|pc2)-[0-9a-f]{8}-a001",
            namespace,
        )
        for namespace in namespaces
    )
    assert all(
        block["history_id"] in block["namespace"]
        for block in first["blocks"]
    )
    assert set(namespaces).isdisjoint(
        {block["namespace"] for block in retry["blocks"]}
    )
    assert all(block["namespace"].endswith("-a002") for block in retry["blocks"])


def test_namespace_changes_for_each_suite_method_history_and_attempt_component() -> None:
    sut = _sut()
    base = sut.baseline_block_namespace(
        suite_run_id="bs-20260816-001",
        method="U0",
        history_id=HISTORIES[0],
        attempt_ordinal=1,
    )

    alternatives = {
        sut.baseline_block_namespace(
            suite_run_id="bs-20260816-002",
            method="U0",
            history_id=HISTORIES[0],
            attempt_ordinal=1,
        ),
        sut.baseline_block_namespace(
            suite_run_id="bs-20260816-001",
            method="A0",
            history_id=HISTORIES[0],
            attempt_ordinal=1,
        ),
        sut.baseline_block_namespace(
            suite_run_id="bs-20260816-001",
            method="U0",
            history_id=HISTORIES[1],
            attempt_ordinal=1,
        ),
        sut.baseline_block_namespace(
            suite_run_id="bs-20260816-001",
            method="U0",
            history_id=HISTORIES[0],
            attempt_ordinal=2,
        ),
    }
    assert base not in alternatives
    assert len(alternatives) == 4


def test_verified_completed_block_is_skipped_and_not_executed_again() -> None:
    sut = _sut()
    block = _plan()["blocks"][0]
    observed = {
        "status": "completed",
        "suite_run_id": "bs-20260816-001",
        "method": block["method"],
        "history_id": block["history_id"],
        "attempt_ordinal": block["attempt_ordinal"],
        "namespace": block["namespace"],
        "artifacts_verified": True,
    }

    assert (
        sut.decide_baseline_block_action(block=block, observed=observed)
        == "SKIP_VERIFIED_COMPLETED"
    )


@pytest.mark.parametrize(
    "status",
    ["running", "failed", "incomplete", "incomplete_non_mergeable"],
)
def test_incomplete_block_fails_closed_and_never_resumes_in_place(status: str) -> None:
    sut = _sut()
    block = _plan()["blocks"][0]
    observed = {
        "status": status,
        "suite_run_id": "bs-20260816-001",
        "method": block["method"],
        "history_id": block["history_id"],
        "attempt_ordinal": block["attempt_ordinal"],
        "namespace": block["namespace"],
        "artifacts_verified": False,
    }

    with pytest.raises(
        sut.BaselineSuiteError,
        match="incomplete|non.mergeable|new.attempt|resume",
    ):
        sut.decide_baseline_block_action(block=block, observed=observed)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(artifacts_verified=False),
        lambda value: value.update(namespace="foreign"),
        lambda value: value.update(method="A0"),
        lambda value: value.update(attempt_ordinal=2),
    ],
)
def test_completed_block_is_skipped_only_after_exact_identity_verification(mutation) -> None:
    sut = _sut()
    block = _plan()["blocks"][0]
    observed = {
        "status": "completed",
        "suite_run_id": "bs-20260816-001",
        "method": block["method"],
        "history_id": block["history_id"],
        "attempt_ordinal": block["attempt_ordinal"],
        "namespace": block["namespace"],
        "artifacts_verified": True,
    }
    mutation(observed)

    with pytest.raises(sut.BaselineSuiteError):
        sut.decide_baseline_block_action(block=block, observed=observed)


def test_not_started_block_is_the_only_state_authorized_to_run_fresh() -> None:
    sut = _sut()
    block = _plan()["blocks"][0]

    assert (
        sut.decide_baseline_block_action(block=block, observed=None)
        == "RUN_FRESH"
    )


def test_parallel_progress_allows_unordered_unique_subset_not_source_prefix() -> None:
    sut = _sut()

    verified = sut.verify_baseline_block_progress(
        method="P(C=2)",
        expected_sequences=[0, 1, 2, 3],
        completed_sequences=[1, 0, 3],
        status="running",
    )
    assert verified["completed_sequences"] == [1, 0, 3]

    with pytest.raises(sut.BaselineSuiteError, match="duplicate|sequence"):
        sut.verify_baseline_block_progress(
            method="P(C=2)",
            expected_sequences=[0, 1, 2, 3],
            completed_sequences=[1, 0, 1],
            status="running",
        )


@pytest.mark.parametrize("method", ["U0", "A0"])
def test_serial_progress_remains_an_exact_source_prefix(method: str) -> None:
    sut = _sut()

    with pytest.raises(sut.BaselineSuiteError, match="prefix|sequence"):
        sut.verify_baseline_block_progress(
            method=method,
            expected_sequences=[0, 1, 2, 3],
            completed_sequences=[1, 0],
            status="running",
        )


def test_parallel_completed_state_requires_full_set_but_not_completion_order() -> None:
    sut = _sut()

    verified = sut.verify_baseline_block_progress(
        method="P(C=2)",
        expected_sequences=[0, 1, 2, 3],
        completed_sequences=[1, 0, 3, 2],
        status="completed",
    )
    assert set(verified["completed_sequences"]) == {0, 1, 2, 3}

    with pytest.raises(sut.BaselineSuiteError, match="complete|sequence"):
        sut.verify_baseline_block_progress(
            method="P(C=2)",
            expected_sequences=[0, 1, 2, 3],
            completed_sequences=[1, 0, 3],
            status="completed",
        )


def test_reuse_u0_is_hash_verified_reference_not_a_namespace_reuse() -> None:
    sut = _sut()
    plan = sut.build_baseline_suite_plan(
        "bs-20260816-001",
        mode="development",
        reuse_u0_run="nb-20260816-001",
    )
    copied = deepcopy(plan)

    assert plan["reuse_u0_run"] == "nb-20260816-001"
    assert all(
        block["namespace"].startswith("pev3-bs-20260816-001-u0-")
        for block in plan["blocks"][:4]
    )
    copied["reuse_u0_run"] = "../unsafe"
    with pytest.raises(sut.BaselineSuiteError, match="reuse|run.id"):
        sut.verify_baseline_suite_plan(copied)
