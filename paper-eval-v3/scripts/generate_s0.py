#!/usr/bin/env python3
"""Generate the v3 S0 artifacts from already-persisted, read-only evidence."""

from __future__ import annotations

import subprocess
from pathlib import Path

from paper_eval.s0_audit import build_s0_artifacts


ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / "membind-validation"
PROTOCOL = ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
OUTPUT = ROOT / "paper-eval-v3/artifacts/paper_eval"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main() -> None:
    working_tree_lines = git("status", "--porcelain=v1").splitlines()
    current_model_revision = "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df"
    c2_model_revision = "6e2312b85c2ae9a31f629f24493b79d8b02eab1a"
    source_hashes = {
        "legacy_split": "b40acce61defe0c809636dc9964cbfa8591fafde5e6330b81ed0e8214bcd71f7",
        "v1_3_split": "747946a8792422ea35e9d56b864efb1a137cb6eb8a8e16f97808fe86f938c091",
        "u0_runtime_source": "4d6bad43289d7cbf9557aed05571601bdfa560855ed1403b0e4a72770ae57ca1",
        "instrumentation_source": "9b386856fb32d2e80f465653374b630d5706a2e1b32fb5b938ede98220148bd7",
        "phase_map": "afdfd18d17e285fe5b23d9ba8eed2cb893ddabb71723259947a3e7317bd72f31",
        "c1_qualification": "3465a1e3b5a340debe53008111f4391376d7e28e76b7ec4941cbade2374ba328",
        "c2_harness_source": "b5c98064052d33c9e4db10ba5f051a0097db43b3c696eddaaef25f1dbfa985d2",
        "c2_completed_manifest": "f03276ef88bfdc8062967db504514c83d941d37f929a8dbca5c37fab7aa69417",
        "c2_completed_checkpoint": "bee2e1a0e2130d6c9f3f579829680b64a3b732b814b7a09a2115f28042e42235",
        "official_evaluator_vendor": "1fc8b73d7a2c84aa7dc24380980380116d1a00f582a1d9667e85cef90856ac61",
        "longmemeval_adapter": "21beccbefcbb1fedcca984e82c72003e0ae3748b4ec6ce0fe544f7e44b048869",
        "judge_qualification_summary": "31a16da6d8a668517315ec22c5b375f9c494f34dbb5c62428be86c39dd028485",
        "judge_runtime_identity": "65b1022da9cc761017cc8d5096165d4fdc8857070382164d81e90694b0509f02",
    }
    identities = {
        "graphiti": {
            "version": "0.29.3",
            "repository_commit": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        },
        "construction": {
            "served_model_id": "qwen3-32b-fp8",
            "repository_revision": current_model_revision,
            "dtype": "bfloat16",
            "quantization": "fp8",
            "vllm_version": "0.26.0",
            "max_model_len": 65536,
            "requested_max_tokens": 16384,
            "structured_output_mode": "json_schema",
            "enable_thinking": False,
            "rope_parameters": {
                "rope_type": "yarn",
                "factor": 2.0,
                "original_max_position_embeddings": 32768,
                "rope_theta": 1000000,
            },
        },
        "embedding": {
            "served_model_id": "qwen3-embedding-0.6b",
            "dimension": 1024,
            "dtype": "bfloat16",
            "pooling": "last_token",
            "normalization": "l2",
            "instruction_policy": "none",
            "deployment_fingerprint": "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626",
        },
        "neo4j": {
            "deployment": "local_non_docker_community",
            "version": "5.26.0",
            "uri": "bolt://localhost:7687",
            "live_readiness_checked_by_s0": False,
        },
        "judge": {
            "qualification": "PASS",
            "agreement": "14/14",
            "cohens_kappa": 1.0,
            "synthetic_fixture": True,
        },
        "reader": {"status": "to_be_frozen_by_protocol_before_quality_execution"},
        "c2_identity_note": {
            "c2_model_revision": c2_model_revision,
            "current_model_revision": current_model_revision,
            "numeric_reuse": "HISTORICAL_ONLY_PENDING_S2_U0_EQUIVALENCE_DECISION",
        },
    }
    reuse = {
        "C0_serving_viability": "REUSE_ENGINEERING_VIABILITY_ONLY",
        "C1_instrumentation_qualification": "REUSE",
        "C2_harness_schema_checkpoint": "REUSE",
        "C2_execution_evidence": "HISTORICAL_ONLY_PENDING_S2_U0_EQUIVALENCE_DECISION",
        "C3_dependency_framework": "REUSE",
        "C3_numeric_bounds": "HISTORICAL_ONLY",
        "C4_results": "HISTORICAL_NON_MERGEABLE_ONLY",
        "C5_durability_concurrency_framework": "REUSE",
        "C5_results": "HISTORICAL_PROBLEM_EVIDENCE_ONLY",
        "official_evaluator_adapter": "REUSE_FOR_S2_ALIGNMENT",
        "judge_qualification": "REUSE_QUALIFICATION_ONLY",
    }
    build_s0_artifacts(
        repo_root=ROOT,
        protocol_path=PROTOCOL,
        output_root=OUTPUT,
        dataset_path=DATASET,
        exposed_ids={"07741c45", "b6019101", "6071bd76", "a2f3aa27", "c6853660"},
        git_commit=git("rev-parse", "HEAD"),
        working_tree_status="dirty" if working_tree_lines else "clean",
        identities=identities,
        source_hashes=source_hashes,
        reuse_decisions=reuse,
        role_metadata={
            "manifest_listed_not_outcome_exposed": [
                "b01defab",
                "0f05491a",
                "6aeb4375",
                "06db6396",
                "89941a94",
                "c4ea545c",
                "ce6d2d27",
                "08e075c7",
            ],
            "judge_real_benchmark_ids_exposed": [],
            "exposure_evidence_policy": "persisted_execution_or_method_outcome_inspection",
        },
    )


if __name__ == "__main__":
    main()

