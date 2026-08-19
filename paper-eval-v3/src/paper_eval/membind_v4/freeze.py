"""Fail-closed v4 method freeze for the autoresearch lane.

The freeze is deliberately small: it binds the first qualified candidate to
the already-registered P0 evidence and records every policy threshold needed
by the formal runner.  A frozen method is immutable by contract; changing any
field invalidates its payload hash and requires a new development decision.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.membind_v4.autoresearch import assess_candidate, candidate_config


FORMAL_HISTORY_IDS = ("07741c45", "6071bd76", "a2f3aa27", "b6019101")


class V4FreezeError(ValueError):
    """A candidate cannot be made the formal v4 method."""


def _fail(code: str) -> V4FreezeError:
    return V4FreezeError(code)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _fail(f"artifact_unreadable:{path}") from error
    if not isinstance(value, dict):
        raise _fail(f"artifact_not_object:{path}")
    return value


def _verify_payload(value: Mapping[str, Any], code: str) -> dict[str, Any]:
    body = dict(value)
    digest = body.pop("payload_sha256", None)
    if not isinstance(digest, str) or digest != payload_sha256(body):
        raise _fail(code)
    return dict(value)


def _binding(path: Path, *, role: str) -> dict[str, object]:
    body = _verify_payload(_read(path), f"{role}_payload_hash_mismatch")
    return {
        "role": role,
        "absolute_path": str(Path(path).resolve()),
        "sha256": sha256_file(Path(path)),
        "schema_version": body.get("schema_version"),
        "status": body.get("status"),
    }


def _git_commit() -> str:
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    return value if value else "UNKNOWN"


def build_frozen_method(
    *,
    candidate_root: Path,
    baseline_binding_path: Path,
    role_profile_path: Path,
    prefix_reference_path: Path,
    output_root: Path,
    focused_test: Mapping[str, object] | None = None,
    code_commit: str | None = None,
) -> dict[str, object]:
    """Freeze a candidate only after the pre-registered FREEZE decision."""

    root = Path(candidate_root)
    candidate = _verify_payload(_read(root / "candidate.json"), "candidate_payload_hash_mismatch")
    summary = _verify_payload(_read(root / "summary.json"), "summary_payload_hash_mismatch")
    reduction = _verify_payload(_read(root / "reduction.json"), "reduction_payload_hash_mismatch")
    if candidate.get("status") != "COMPLETED":
        raise _fail("candidate_manifest_not_completed")
    decision = reduction.get("decision")
    if not isinstance(decision, Mapping) or decision.get("decision") != "FREEZE":
        raise _fail("candidate_not_freeze")
    source_count = int(summary.get("source_count", 0) or 0)
    if source_count != 12:
        raise _fail("freeze_requires_decision_prefix")
    if int(summary.get("direct_violation_count", 0) or 0) != 0:
        raise _fail("freeze_correctness_violation")
    mechanism = reduction.get("mechanism")
    performance = reduction.get("performance")
    if not isinstance(mechanism, Mapping) or not isinstance(performance, Mapping):
        raise _fail("candidate_reduction_evidence_invalid")
    mechanism_fields = (
        "qualified_node_resolve_count",
        "speculation_launch_count",
        "exact_validation_completed_count",
        "semantic_hit_count",
        "semantic_miss_count",
        "overlap_count",
        "hidden_critical_time_ns",
        "direct_violation_count",
    )
    if any(mechanism.get(field, 0) != summary.get(field, 0) for field in mechanism_fields):
        raise _fail("candidate_mechanism_evidence_drift")
    if "freshness_p95_ratio" not in performance or "makespan_ratio" not in performance:
        raise _fail("candidate_performance_evidence_invalid")
    recomputed = assess_candidate(
        {
            **dict(summary),
            **{field: mechanism.get(field, 0) for field in mechanism_fields},
            "freshness_p95_ratio": performance.get("freshness_p95_ratio"),
            "makespan_ratio": performance.get("makespan_ratio"),
        }
    )
    if recomputed != dict(decision):
        raise _fail("candidate_decision_drift")
    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str):
        raise _fail("candidate_id_invalid")
    if summary.get("runner_mode") != "live":
        raise _fail("freeze_requires_live_candidate")
    if (
        summary.get("status") != "PASS"
        or summary.get("history_id") != "07741c45"
        or candidate.get("source_count") != source_count
        or reduction.get("candidate_id") != candidate_id
        or reduction.get("source_count") != source_count
        or reduction.get("status") != summary.get("status")
    ):
        raise _fail("candidate_evidence_identity_drift")
    config = candidate_config(candidate_id)
    # The persisted candidate must agree with the pre-registered policy.
    for key in ("policy", "global_k", "speculation_distance", "phase_complementary"):
        if candidate.get(key) != config.get(key):
            raise _fail("candidate_policy_drift")

    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.frozen-method.v1",
        "status": "FROZEN",
        "candidate_id": candidate_id,
        "policy": config["policy"],
        "policy_config": config,
        "thresholds": {
            "global_k": 2,
            "speculation_distance": 1,
            "max_frontier_interference_ratio": 1.05,
            "max_useful_throughput_drop_ratio": 0.05,
            "min_makespan_or_freshness_gain_ratio": 0.05,
        },
        "fixed_runtime": {
            "compile_workers": "V31_FROZEN",
            "lookahead": "V31_FROZEN",
            "structured_output_policy": "V31_FROZEN",
        },
        "formal_history_ids": list(FORMAL_HISTORY_IDS),
        "development_prefix": {"history_id": summary.get("history_id"), "source_count": 12},
        "evidence": {
            "candidate": _binding(root / "candidate.json", role="candidate"),
            "summary": _binding(root / "summary.json", role="summary"),
            "reduction": _binding(root / "reduction.json", role="reduction"),
            "baseline_binding": _binding(Path(baseline_binding_path), role="baseline_binding"),
            "role_profile": _binding(Path(role_profile_path), role="role_profile"),
            "prefix_reference": _binding(Path(prefix_reference_path), role="prefix_reference"),
        },
        "focused_test": dict(focused_test or {"status": "NOT_RECORDED"}),
        "code_commit": code_commit or _git_commit(),
        "autoresearch_decision": dict(decision),
    }
    body["payload_sha256"] = payload_sha256(body)
    target = Path(output_root)
    target.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target / "V4_FROZEN_METHOD.json", body)
    markdown = _markdown(body)
    (target / "V4_FROZEN_METHOD.md").write_text(markdown, encoding="ascii")
    return body


def verify_frozen_method(path: Path) -> dict[str, Any]:
    body = _verify_payload(_read(Path(path)), "frozen_method_payload_hash_mismatch")
    if body.get("schema_version") != "membind.paper-eval-v4.frozen-method.v1":
        raise _fail("frozen_method_schema_invalid")
    if body.get("status") != "FROZEN":
        raise _fail("frozen_method_status_invalid")
    if tuple(body.get("formal_history_ids", ())) != FORMAL_HISTORY_IDS:
        raise _fail("formal_history_set_drift")
    thresholds = body.get("thresholds")
    if not isinstance(thresholds, Mapping) or thresholds.get("global_k") != 2:
        raise _fail("frozen_thresholds_invalid")
    return body


def _markdown(body: Mapping[str, object]) -> str:
    return "\n".join(
        [
            "# MemBind v4 Frozen Method",
            "",
            f"- Status: `{body['status']}`",
            f"- Candidate: `{body['candidate_id']}`",
            f"- Policy: `{body['policy']}`",
            "- Global K: `2`",
            "- Speculation distance: `1`",
            "- Formal histories: `07741c45, 6071bd76, a2f3aa27, b6019101`",
            f"- Code commit: `{body['code_commit']}`",
            "",
            "This method is immutable after formal execution starts.",
            "",
        ]
    )


__all__ = ["FORMAL_HISTORY_IDS", "V4FreezeError", "build_frozen_method", "verify_frozen_method"]
