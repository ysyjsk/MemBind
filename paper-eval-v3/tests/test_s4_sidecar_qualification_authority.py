"""TDD for the single-use fixed-three S4 qualification authority."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval import PROTOCOL_VERSION
from paper_eval.artifacts import finalize_envelope, payload_sha256, sha256_file
from paper_eval.s4_sidecar_qualification_authority import (
    AUTHORITY_SOURCE_NAMES,
    build_s4_sidecar_qualification_authority,
    consume_s4_sidecar_qualification_authority,
    verify_s4_sidecar_qualification_authority,
    verify_s4_sidecar_qualification_authority_consumption,
)


PROJECT = Path(__file__).resolve().parents[1]
PLAN_PATH = PROJECT / "artifacts/paper_eval/native/S4_D0_QUALIFICATION_PLAN.json"


def _plan() -> dict:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def _activation(plan: dict) -> dict:
    return finalize_envelope(
        payload={
            "schema_version": (
                "membind.paper-eval-v3.s4-sidecar-qualification-activation.v3"
            ),
            "stage": "S4_QUALIFICATION_ACTIVATION",
            "status": "ACTIVATED_BY_VERIFIED_SIDECAR_SMOKE_PASS",
            "qualification_plan": {
                "file_sha256": sha256_file(PLAN_PATH),
                "plan_sha256": plan["plan_sha256"],
                "common_method_policy_sha256": plan[
                    "common_method_policy_sha256"
                ],
            },
            "verified_smoke": {
                "run_id": "s4-d0-sidecar-smoke-result-20260815-008",
                "verdict": "PASS",
                "history_id": "07741c45",
            },
            "activated_projection": {
                "reused_smoke_history_id": "07741c45",
                "live_history_ids": ["b6019101", "6071bd76", "a2f3aa27"],
                "live_blocks_sha256": payload_sha256(plan["blocks"][1:]),
                "sequential_blocks": True,
                "next_block_requires_prior_pass": True,
            },
            "authority": {
                "qualification_live_authorized": True,
                "s5_authorized": False,
                "pilot_execution_authorized": False,
            },
            "source_sha256": {"activation": "a" * 64},
        },
        protocol_version=PROTOCOL_VERSION,
        git_commit="deadbeef",
        run_id="s4-sidecar-qualification-activation-20260815-003",
    )


def _bindings() -> list[dict]:
    return [
        {
            "history_id": history_id,
            "episode_count": count,
            "episode_manifest_sha256": character * 64,
        }
        for history_id, count, character in (
            ("b6019101", 49, "1"),
            ("6071bd76", 46, "2"),
            ("a2f3aa27", 44, "3"),
        )
    ]


def _source_sha256() -> dict[str, str]:
    return {name: "f" * 64 for name in AUTHORITY_SOURCE_NAMES}


def _authority() -> dict:
    plan = _plan()
    activation = _activation(plan)
    return build_s4_sidecar_qualification_authority(
        qualification_plan=plan,
        qualification_plan_file_sha256=sha256_file(PLAN_PATH),
        activation=activation,
        activation_file_sha256="4" * 64,
        dataset_file_sha256="5" * 64,
        split_file_sha256=plan["input_file_sha256"]["split"],
        history_bindings=_bindings(),
        source_sha256=_source_sha256(),
        git_commit="deadbeef",
    )


def test_authority_projects_only_remaining_blocks_and_derives_private_sidecars() -> None:
    authority = verify_s4_sidecar_qualification_authority(_authority())
    payload = authority["payload"]

    assert [block["history"]["history_id"] for block in payload["blocks"]] == [
        "b6019101",
        "6071bd76",
        "a2f3aa27",
    ]
    assert [block["history"]["episode_count"] for block in payload["blocks"]] == [
        49,
        46,
        44,
    ]
    for block in payload["blocks"]:
        cache_id = block["plan_block"]["cache_id"]
        assert block["private_cache"]["candidate_sidecar_relpath"] == (
            f"runtime/private/{cache_id}/candidate-sidecar.jsonl"
        )
        assert block["private_cache"]["prompt_relpath"] == block["plan_block"][
            "private_cache"
        ]["prompt_relpath"]
    assert payload["scope"] == {
        "single_use": True,
        "fixed_three_pipeline_authorized": True,
        "capture_before_replay": True,
        "sequential_blocks": True,
        "next_block_requires_prior_pass": True,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["payload"]["blocks"].reverse(),
        lambda value: value["payload"]["blocks"][1]["history"].update(
            episode_count=49
        ),
        lambda value: value["payload"]["scope"].update(s5_authorized=True),
        lambda value: value["payload"].update(raw_response="private"),
    ],
)
def test_authority_tamper_fails_closed(mutate) -> None:
    artifact = copy.deepcopy(_authority())
    mutate(artifact)
    artifact = finalize_envelope(
        payload=artifact["payload"],
        protocol_version=artifact["protocol_version"],
        git_commit=artifact["git_commit"],
        run_id=artifact["run_id"],
    )
    with pytest.raises(ValueError):
        verify_s4_sidecar_qualification_authority(artifact)


def test_authority_consumption_is_exclusive_and_exactly_bound(tmp_path: Path) -> None:
    authority = _authority()
    output = tmp_path / "consumption.json"
    consumed = consume_s4_sidecar_qualification_authority(
        authority=authority,
        authority_file_sha256="6" * 64,
        output_path=output,
        git_commit="deadbeef",
    )

    verified = verify_s4_sidecar_qualification_authority_consumption(consumed)
    assert verified["payload"]["authority_payload_sha256"] == authority[
        "payload_sha256"
    ]
    assert verified["payload"]["consumed_action"] == (
        "S4_FIXED_THREE_SIDECAR_QUALIFICATION_PIPELINE"
    )
    with pytest.raises(FileExistsError):
        consume_s4_sidecar_qualification_authority(
            authority=authority,
            authority_file_sha256="6" * 64,
            output_path=output,
            git_commit="deadbeef",
        )
