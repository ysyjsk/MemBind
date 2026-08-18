"""Identity and scope tests for the methodology-v3.1 execution overlay.

The historical v3.0 protocol is an immutable parent for existing artifacts.
The versioned v3.1 workplan is the only authority for new MemBind runs.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
NEW = ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3.1_METHODOLOGY_ALIGNED.md"
METHODOLOGY = ROOT / "MemBind_FINAL_METHODOLOGY_v3.1_FROZEN.md"
EXECUTION = ROOT / "paper-eval-v3/EXECUTION_PLAN.md"

OLD_SHA256 = "4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e"
NEW_SHA256 = "f81c186e7cccd75ba6c1e15628b5e9a90a4484e20a6b4e27ac126aa80763c479"
METHODOLOGY_SHA256 = (
    "2af8147a839972e120a0123d1d0fffdd3e10653fa64e338c1c7a7fa32f506280"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_historical_parent_remains_byte_identical() -> None:
    assert _sha256(OLD) == OLD_SHA256


def test_v31_authorities_are_hash_bound() -> None:
    assert _sha256(NEW) == NEW_SHA256
    assert _sha256(METHODOLOGY) == METHODOLOGY_SHA256

    execution = EXECUTION.read_text(encoding="utf-8")
    assert str(NEW.relative_to(ROOT)) in execution
    assert NEW_SHA256 in execution
    assert METHODOLOGY_SHA256 in execution


def test_v31_workplan_contains_the_frozen_method_and_reuse_boundaries() -> None:
    text = NEW.read_text(encoding="utf-8")
    required = (
        "Arrival Eligibility",
        "State-Cut Compilation",
        "Prepared Reorder Buffer",
        "frontier-first Version-Bound Bind",
        "MemBind-Barrier",
        "MemBind-FIFO",
        "STATE_CUT_CERTIFICATION_FAILURE",
        "Compile-before-arrival",
        "rho_C_req",
        "Schedule-Eligible Reusable Prefix Tokens",
        "apc-aligned-pipeline-20260817-001",
        "apc-baseline-dev-20260817-001",
        "MemBind-v1 node-only",
        "DEVELOPMENT_EXPOSED",
        "STOP before PILOT",
    )
    for phrase in required:
        assert phrase in text

    assert "FINAL_PAPER_TEST access" in text
    assert "must not be rerun merely" in text
    assert "If EdgeExtract does not qualify" in text
    assert "six full-history blocks total" in text
    assert "blocks 0-3: MemBind on all four histories" in text


def test_v31_workplan_keeps_cache_claim_secondary_and_fail_closed() -> None:
    text = NEW.read_text(encoding="utf-8")
    assert "vLLM APC itself is not a MemBind contribution" in text
    assert "Aggregate APC hit rate alone cannot support the cache claim" in text
    assert "labels cache effects `OBSERVATIONAL`" in text
    assert "no silent fallback" in text.casefold()


def test_v31_workplan_freezes_exact_method_backend_and_transport_contract() -> None:
    text = NEW.read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    required = (
        "compile workers `C = 2`",
        "lookahead `W = 2`",
        "global LLM admission `K_LLM = 2`",
        "prefix-match granularity `G = 16 tokens`",
        "decode-context parallel size `DCP = 1`",
        "one actual outbound construction-model transport attempt",
        "Every retry or HTTP transport attempt reacquires admission independently",
        "observed actual transport-attempt inflight",
    )
    for phrase in required:
        assert phrase in normalized


def test_v31_workplan_freezes_comparable_cache_isolation_and_artifact_authority() -> None:
    text = NEW.read_text(encoding="utf-8")
    required = (
        "U0/A0/P(C=2)/MemBind-Barrier/MemBind-FIFO/MemBind",
        "unique fresh request cache salt",
        "cross_block_prefix_identity_reuse = false",
        "cross_block_warm_inheritance = false",
        "within_block_prefix_reuse = true",
        "physical_cache_reset_claimed = false",
        "V31_REUSE_AUDIT.json` is the V0 offline source/hash/reuse audit only",
        "V31_BASELINE_ACCEPTANCE.json` is emitted only at V3",
        "six-file V0 freezer never emits `V31_METHOD_PLAN.json`",
        "V31_CONTROL_COMMIT.json",
        "LIVE_EXECUTION_AUTHORIZED_BASELINE_MERGE_PENDING",
        "Baseline acceptance blocks final merge, not V4-V6 live",
    )
    for phrase in required:
        assert phrase in text


def test_v31_workplan_separates_artifact_semantics_and_harness_durability() -> None:
    text = " ".join(NEW.read_text(encoding="utf-8").split())
    required = (
        "artifact_status = SEALED_VALID | INVALID_INFRA | INCOMPLETE",
        "semantic_status = SAFE | VIOLATION_OBSERVED | NOT_APPLICABLE",
        "SEALED_VALID + VIOLATION_OBSERVED",
        "not a crash-consistency claim",
        "does not authorize building a general recovery subsystem",
        "predeclared representative history `07741c45`",
    )
    for phrase in required:
        assert phrase in text


def test_v31_workplan_freezes_zero_live_cost_diagnostics_and_final_quality_gate() -> None:
    text = " ".join(NEW.read_text(encoding="utf-8").split())
    required = (
        "V31_WORKLOAD_COMPLEXITY.json",
        "source-turn construction rate",
        "source-input-token construction rate",
        "per-history makespan speedup versus U0",
        "four exposed histories do not authorize a significance test",
        "does not block performance characterization",
        "Any later final-paper Claim C requires a qualified common quality evaluator",
        "balanced/counterbalanced method order",
    )
    for phrase in required:
        assert phrase in text


def test_v31_workplan_defines_bounded_autoresearch_probe_without_main_table_authority() -> None:
    text = " ".join(NEW.read_text(encoding="utf-8").split())
    required = (
        "autoresearch development probe",
        "fixed 12-episode prefix",
        "results.tsv",
        "keep / discard / crash",
        "new namespace and cache salt for every candidate",
        "probe artifacts are non-mergeable",
        "C = W = K_LLM = 2 cannot be changed",
        "no prompt, schema, source, arrival-trace or evaluator changes",
        "maximum of three candidate iterations",
        "zero direct violations",
        "p95 freshness and makespan",
        "rerun the full offline suite",
    )
    for phrase in required:
        assert phrase in text
