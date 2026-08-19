from __future__ import annotations

import asyncio

from mab_quality_v2_final_qa.contracts import canonical_sha256
from mab_quality_v2_final_qa.reducer import reduce_method_rows, reduce_paired_rows
from mab_quality_v2_final_qa.report import render_final_report

from .test_runner_one_build_many_qa import _runner


def test_reducer_excludes_invalid_and_reports_paired_disagreements(tmp_path) -> None:
    u0_runner, context, *_ = _runner(tmp_path / "u0")
    u0 = list(asyncio.run(u0_runner.run_context(context)))
    mb_runner, _, *_ = _runner(tmp_path / "mb", method_id="MEMBIND_V31")
    mb = list(asyncio.run(mb_runner.run_context(context)))
    u0[0]["correct"] = False
    u0[0]["payload_sha256"] = canonical_sha256(
        {k: v for k, v in u0[0].items() if k != "payload_sha256"}
    )
    mb[1]["correct"] = False
    mb[1]["payload_sha256"] = canonical_sha256(
        {k: v for k, v in mb[1].items() if k != "payload_sha256"}
    )
    u0[2].update(
        {
            "status": "INVALID",
            "judge_valid": False,
            "correct": None,
            "failure_class": "JUDGE_INVALID",
        }
    )
    u0[2]["payload_sha256"] = canonical_sha256(
        {k: v for k, v in u0[2].items() if k != "payload_sha256"}
    )
    summary = reduce_method_rows(u0, method="U0", bootstrap_samples=100)
    paired = reduce_paired_rows(u0, mb, bootstrap_samples=100)
    assert summary["valid_judge_count"] == 4
    assert summary["invalid_judge_count"] == 1
    assert summary["qa_accuracy"] == 0.75
    assert summary["failure_decomposition"] == {"JUDGE_INVALID": 1}
    assert paired["paired_disagreements"]["u0_only_correct"] == 1
    assert paired["paired_disagreements"]["membind_only_correct"] == 1
    assert paired["paired_disagreements"]["invalid_u0"] == 1
    report = render_final_report(
        paired,
        run_id="offline-test",
        dataset_manifest_sha256="d" * 64,
        freeze_sha256="f" * 64,
    )
    assert "Invalid Judge" in report
    assert "Context-cluster bootstrap delta" in report
