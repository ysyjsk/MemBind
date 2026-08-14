"""Build S2 alignment artifacts without contacting model or database services."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from paper_eval.artifacts import atomic_write_json, finalize_envelope, sha256_file
from paper_eval.s2_alignment import (
    dataset_projection_parity,
    decide_c2_u0_reuse,
    evaluator_parity,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = Path("/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json")
SPLIT = ROOT.parent / "membind-validation/artifacts/dataset/frozen_split_v1_3.json"
C2_MANIFEST = ROOT.parent / "membind-validation/artifacts/native_characterization/runs/c2-17cdaabd562e9673/manifest.json"
S0 = ROOT / "artifacts/paper_eval/S0_CURRENT_STATE.json"
OUT = ROOT / "artifacts/paper_eval/native"
LEGACY_SRC = ROOT.parent / "membind-validation/src"
FIXTURE = ROOT.parent / "membind-validation/fixtures/judge_qualification_14_v1.json"
DATASET_BUILDER = LEGACY_SRC / "dataset.py"
JUDGE_UPSTREAM_MANIFEST = (
    ROOT.parent / "membind-validation/artifacts/protocol/judge_upstream_manifest_20260812.json"
)
JUDGE_STRICT_FREEZE = (
    ROOT.parent
    / "membind-validation/artifacts/protocol/judge_qualification_strict_freeze_20260813.json"
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _envelope(payload: dict[str, Any], run_id: str, commit: str) -> dict[str, Any]:
    return finalize_envelope(payload=payload, protocol_version="paper-eval-v3", git_commit=commit, run_id=run_id)


def _legacy_imports() -> tuple[Any, ...]:
    source = str(LEGACY_SRC)
    if source not in sys.path:
        sys.path.insert(0, source)
    from dataset import build_episodes
    from evaluation.backends.base import JudgeBackendResult
    from evaluation.benchmarks.longmemeval import (
        LongMemEvalAdapter,
        official_compatible_label,
        parse_audit_label,
    )
    from evaluation.provenance import validate_judge_upstream_manifest
    from evaluation.schemas import EvaluationItem
    from evaluation.vendor.longmemeval_evaluate_qa import get_anscheck_prompt

    return (
        build_episodes,
        JudgeBackendResult,
        LongMemEvalAdapter,
        EvaluationItem,
        get_anscheck_prompt,
        official_compatible_label,
        parse_audit_label,
        validate_judge_upstream_manifest,
    )


async def _actual_evaluator_routes() -> dict[str, dict[str, Any]]:
    (
        _build_episodes,
        JudgeBackendResult,
        LongMemEvalAdapter,
        EvaluationItem,
        get_anscheck_prompt,
        official_compatible_label,
        parse_audit_label,
        _validate_judge_upstream_manifest,
    ) = _legacy_imports()

    class CaptureBackend:
        model = "offline-fixture"
        config_hash = "0" * 64

        def __init__(self, output: str) -> None:
            self.output = output
            self.prompts: list[str] = []

        async def judge(self, prompt: str) -> Any:
            self.prompts.append(prompt)
            return JudgeBackendResult.success(raw_output=self.output, retry_count=0)

    routes: dict[str, dict[str, Any]] = {}
    for record in _load(FIXTURE)["items"]:
        output = "yes" if record["human_label"] else "no"
        backend = CaptureBackend(output)
        item = EvaluationItem(
            item_id=str(record["item_id"]),
            benchmark="longmemeval",
            question_id=str(record["question_id"]),
            question_type=str(record["question_type"]),
            question=str(record["question"]),
            reference_answer=str(record["reference_answer"]),
            hypothesis=str(record["hypothesis"]),
            abstention=bool(record["abstention"]),
        )
        result = await LongMemEvalAdapter(backend).evaluate(item)
        official_prompt = get_anscheck_prompt(
            record["question_type"],
            record["question"],
            record["reference_answer"],
            record["hypothesis"],
            record["abstention"],
        )
        routes[str(record["item_id"])] = {
            "question_type": record["question_type"],
            "abstention": bool(record["abstention"]),
            "official_prompt": official_prompt,
            "adapter_prompt": backend.prompts[0],
            "official_label": "yes" in output.lower(),
            "adapter_label": result.label,
            "official_raw_output": output,
            "adapter_raw_output": result.raw_output,
            "adapter_status": result.status.value,
        }
    for output in ("yes", "no", "yesterday", "yes and no", "maybe"):
        audit = parse_audit_label(output)
        route_id = f"parser-{output.replace(' ', '-')}"
        routes[route_id] = {
            "question_type": "parser-semantics",
            "abstention": False,
            "official_prompt": route_id,
            "adapter_prompt": route_id,
            "official_label": official_compatible_label(output),
            "adapter_label": official_compatible_label(output),
            "official_raw_output": output,
            "adapter_raw_output": output,
            "adapter_status": "SUCCESS" if audit.label is not None else "INVALID_OUTPUT",
        }
    return routes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="s2-20260814-001")
    args = parser.parse_args()
    if (OUT / "U0_REFERENCE_SANITY.json").exists():
        raise RuntimeError("refusing to overwrite finalized S2 sanity")
    OUT.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT.parent, text=True).strip()
    records = _load(DATASET)
    split = _load(SPLIT)
    ids = [*split["calibration_question_ids"], split["compatibility_development_question_ids"][0]]

    build_episodes, *_ = _legacy_imports()
    selected = {str(record.get("question_id")): record for record in records}
    projections = []
    for question_id in ids:
        record = selected[question_id]
        episodes = list(build_episodes(record))
        projections.append(
            {
                "question_id": question_id,
                "question_type": record["question_type"],
                "session_ids": [episode.session_id for episode in episodes],
                "timestamps": [episode.reference_time for episode in episodes],
                "answer_session_ids": [str(value) for value in record["answer_session_ids"]],
                "question_sha256": hashlib.sha256(str(record["question"]).encode()).hexdigest(),
                "answer_sha256": hashlib.sha256(str(record["answer"]).encode()).hexdigest(),
                "episode_body_hashes": [
                    hashlib.sha256(episode.body.encode()).hexdigest()
                    for episode in episodes
                ],
                "episode_source_hashes": [episode.source_hash for episode in episodes],
            }
        )
    parity = dataset_projection_parity(
        records,
        projections,
        ids,
        episode_builder=build_episodes,
    )
    parity["source_sha256"] = sha256_file(DATASET)
    parity["episode_builder_source"] = "membind-validation/src/dataset.py:build_episodes"
    parity["episode_builder_source_sha256"] = sha256_file(DATASET_BUILDER)
    parity["independent_copy_available"] = False
    parity["interpretation"] = "official_cleaned_source_vs_runtime_episode_projection"
    atomic_write_json(OUT / "DATASET_PARITY.json", _envelope(parity, args.run_id, commit))

    *_, validate_judge_upstream_manifest = _legacy_imports()
    validate_judge_upstream_manifest(
        _load(JUDGE_UPSTREAM_MANIFEST), ROOT.parent / "membind-validation"
    )
    routes = asyncio.run(_actual_evaluator_routes())
    frozen_hashes = {
        str(item["item_id"]): str(item["official_prompt_sha256"])
        for item in _load(JUDGE_STRICT_FREEZE)["items"]
    }
    frozen_hashes.update(
        {
            fixture_id: hashlib.sha256(str(route["official_prompt"]).encode()).hexdigest()
            for fixture_id, route in routes.items()
            if fixture_id.startswith("parser-")
        }
    )
    evaluator = evaluator_parity(routes, expected_prompt_hashes=frozen_hashes)
    evaluator["official_rubric_source"] = "membind-validation/src/evaluation/vendor/longmemeval_evaluate_qa.py"
    evaluator["adapter_source"] = "membind-validation/src/evaluation/benchmarks/longmemeval.py"
    evaluator["frozen_rubric_fixture_count"] = len(frozen_hashes) - 5
    evaluator["parser_semantics_fixture_count"] = 5
    evaluator["prompt_content_compared_in_memory_only"] = True
    evaluator["upstream_manifest_validation"] = "strict_regeneration_match"
    evaluator["upstream_manifest_sha256"] = sha256_file(JUDGE_UPSTREAM_MANIFEST)
    evaluator["frozen_prompt_hash_source_sha256"] = sha256_file(JUDGE_STRICT_FREEZE)
    atomic_write_json(OUT / "EVALUATOR_PARITY.json", _envelope(evaluator, args.run_id, commit))

    c2 = _load(C2_MANIFEST)
    s0 = _load(S0)["payload"]
    current_runtime = s0["runtime_identities"]
    verification = json.loads(
        subprocess.check_output(
            [
                str(ROOT.parent / "membind-validation/.venv/bin/python"),
                str(ROOT.parent / "membind-validation/src/native_characterization_c2_verify.py"),
                "--validation-root",
                str(ROOT.parent / "membind-validation"),
                "--run-id",
                str(c2["run_id"]),
            ],
            text=True,
        )
    )
    decision = decide_c2_u0_reuse(
        c2_manifest=c2,
        current_runtime=current_runtime,
        u0_contract={
            "source_hashes": {
                "u0_runtime_source_sha256": s0["source_hashes"]["u0_runtime_source"]
            }
        },
        c2_verification=verification,
    )
    decision["c2_manifest_sha256"] = sha256_file(C2_MANIFEST)
    decision["current_state_sha256"] = sha256_file(S0)
    decision["c2_verification"] = verification
    atomic_write_json(OUT / "C2_U0_REUSE_DECISION.json", _envelope(decision, args.run_id, commit))

    sanity = {
        "stage": "S2",
        "status": "OFFLINE_GATE_ONLY_NO_NUMERIC_RETRIEVAL",
        "retrieval_surface": "NOT_EXECUTED",
        "edge_attributed_source_session_coverage_at_10": None,
        "official_longmemeval_session_recall_at_10": None,
        "qa_accuracy": None,
        "reason": "S2 offline gate does not issue construction/retrieval/judge requests; current Reader/Judge identity is not frozen for numeric sanity",
        "near_zero_stop_evaluable": False,
        "required_next_live_action": "1-history U0 qualification after parity and Case B gates",
    }
    atomic_write_json(OUT / "U0_REFERENCE_SANITY.json", _envelope(sanity, args.run_id, commit))
    print(json.dumps({"dataset": parity["verdict"], "evaluator": evaluator["verdict"], "c2_case": decision["case"], "sanity": sanity["status"]}, sort_keys=True))
    return 0 if parity["verdict"] == evaluator["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
