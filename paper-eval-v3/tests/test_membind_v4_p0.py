"""Offline TDD checks for the v4 P0 result binding artifacts.

These tests intentionally use tiny local fixtures.  P0 is an evidence
registration step: it must not start Graphiti, vLLM, Neo4j, or any other
network service.
"""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.membind_v4.p0_binding import (
    build_baseline_binding,
    build_prefix_reference,
    build_role_profile,
)


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _result(*, method: str, count: int = 12) -> dict[str, object]:
    rows = [
        {
            "source_sequence": index,
            "arrival_timestamp_ns": index * 100,
            "publication_timestamp_ns": index * 100 + 20 + index,
            "freshness_ns": 20 + index,
            "service_latency_ns": 10 + index,
        }
        for index in range(count)
    ]
    body = {
        "schema_version": "fixture.result.v1",
        "status": "PASS",
        "method": method,
        "history_id": "07741c45",
        "performance": {"per_source": rows},
    }
    return body


def test_baseline_binding_records_absolute_paths_and_sha256(tmp_path: Path) -> None:
    """P0 must bind immutable files, rather than copying or rerunning them."""

    v31 = _write_json(tmp_path / "v31" / "RESULT.json", _result(method="MemBind"))
    baseline = _write_json(
        tmp_path / "baseline" / "RESULT.json", _result(method="U0-aligned")
    )

    binding = build_baseline_binding(
        v31_result_path=v31,
        baseline_result_paths=[baseline],
    )

    assert binding["schema_version"] == "membind.paper-eval-v3.membind-v4-baseline-binding.v1"
    assert binding["status"] == "PASS"
    bound_v31 = binding["artifacts"]["v3_1_success"]
    assert bound_v31["absolute_path"] == str(v31.resolve())
    assert len(bound_v31["sha256"]) == 64
    assert binding["artifacts"]["baseline"][0]["absolute_path"] == str(
        baseline.resolve()
    )
    assert binding["payload_sha256"]


def test_role_profile_uses_logical_calls_and_is_deterministic(tmp_path: Path) -> None:
    trace = tmp_path / "llm.jsonl"
    rows = [
        {
            "duration_ns": 100,
            "metadata": {
                "prompt_name": "extract_edges.edge",
                "input_tokens": 2_000,
                "output_tokens": 20,
                "retry_count": 0,
            },
            "operation_class": "logical-call",
            "status": "ok",
        },
        {
            "duration_ns": 200,
            "metadata": {
                "prompt_name": "extract_edges.edge",
                "input_tokens": 4_000,
                "output_tokens": 40,
                "retry_count": 1,
            },
            "operation_class": "logical-call",
            "status": "ok",
        },
        # Transport rows must not be counted as another logical call.
        {
            "duration_ns": 999,
            "metadata": {"input_tokens": 4_000, "output_tokens": 40},
            "operation_class": "request-attempt",
            "status": "ok",
        },
    ]
    trace.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )

    first = build_role_profile([trace])
    second = build_role_profile([trace])

    role = first["roles"]["extract_edges.edge"]
    assert role["logical_call_count"] == 2
    assert role["input_tokens_total"] == 6_000
    assert role["output_tokens_total"] == 60
    assert role["retry_count_total"] == 1
    assert first == second
    assert first["source_traces"][0]["absolute_path"] == str(trace.resolve())
    assert first["payload_sha256"]


def test_prefix_reference_materializes_both_registered_prefixes(tmp_path: Path) -> None:
    v31_path = _write_json(
        tmp_path / "v31" / "RESULT.json", _result(method="MemBind", count=12)
    )
    baseline_path = _write_json(
        tmp_path / "baseline" / "RESULT.json", _result(method="U0-aligned", count=12)
    )

    reference = build_prefix_reference(
        v31_result_path=v31_path,
        baseline_result_paths=[baseline_path],
        history_id="07741c45",
    )

    assert reference["schema_version"] == "membind.paper-eval-v3.membind-v4-prefix-reference.v1"
    assert reference["history_id"] == "07741c45"
    assert reference["prefixes"]["sources_0_5"]["source_count"] == 6
    assert reference["prefixes"]["sources_0_11"]["source_count"] == 12
    assert set(reference["methods"]) == {"MemBind", "U0-aligned"}
    assert reference["prefixes"]["sources_0_5"]["methods"]["MemBind"]["count"] == 6
    assert reference["payload_sha256"]


def test_prefix_reference_falls_back_to_v31_sibling_events(tmp_path: Path) -> None:
    """The v3.1 outer RESULT has aggregate metrics; events remain usable."""

    run_root = tmp_path / "v31"
    result = _write_json(
        run_root / "RESULT.json",
        {
            "status": "PASS",
            "block_result": {
                "method": "MemBind",
                "history_id": "07741c45",
                "performance": {"published_episode_count": 12},
            },
        },
    )
    events: list[str] = []
    sequence = 0
    for source in range(12):
        for event_type, timestamp in (
            ("ARRIVAL", source * 100),
            ("BIND_STARTED", source * 100 + 5),
            ("PUBLICATION_DURABLE", source * 100 + 25),
        ):
            events.append(
                json.dumps(
                    {
                        "event_sequence": sequence,
                        "source_sequence": source,
                        "event_type": event_type,
                        "timestamp_ns": timestamp,
                    },
                    sort_keys=True,
                )
            )
            sequence += 1
    (run_root / "block-00" / "events.jsonl").parent.mkdir(parents=True)
    (run_root / "block-00" / "events.jsonl").write_text(
        "\n".join(events) + "\n", encoding="utf-8"
    )

    baseline = _write_json(
        tmp_path / "baseline" / "RESULT.json", _result(method="U0-aligned", count=12)
    )
    reference = build_prefix_reference(
        v31_result_path=result,
        baseline_result_paths=[baseline],
    )

    assert reference["prefixes"]["sources_0_11"]["methods"]["MemBind"]["count"] == 12
    assert (
        reference["prefixes"]["sources_0_5"]["methods"]["MemBind"]["freshness_ns_mean"]
        == 25.0
    )
