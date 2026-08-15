"""Offline TDD for activating the sealed S4 fixed-four plan after smoke PASS."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval import PROTOCOL_VERSION
from paper_eval.artifacts import finalize_envelope, payload_sha256, sha256_file
from paper_eval.s4_qualification_activation import (
    build_s4_qualification_activation,
    finalize_s4_qualification_activation,
    verify_s4_qualification_activation,
)


PROJECT = Path(__file__).resolve().parents[1]
PLAN_PATH = PROJECT / "artifacts/paper_eval/native/S4_D0_QUALIFICATION_PLAN.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _smoke_result() -> dict:
    return finalize_envelope(
        payload={
            "schema_version": "membind.paper-eval-v3.s4-d0-remap-smoke-result.v2",
            "stage": "S4",
            "verdict": "PASS",
            "authority": {
                "s4_four_history_qualification_authorized": True,
                "s5_authorized": False,
                "pilot_execution_authorized": False,
            },
        },
        protocol_version=PROTOCOL_VERSION,
        git_commit="deadbeef",
        run_id="s4-d0-remap-smoke-result-20260815-005",
    )


def _strict_inputs() -> dict:
    return {
        "authority": {"sentinel": "authority"},
        "authority_file_sha256": "1" * 64,
        "consumption": {"sentinel": "consumption"},
        "consumption_file_sha256": "2" * 64,
        "capture_result": {"sentinel": "capture"},
        "capture_result_file_sha256": "3" * 64,
        "replay_result": {"sentinel": "replay"},
        "replay_result_file_sha256": "4" * 64,
    }


def _build(monkeypatch: pytest.MonkeyPatch) -> tuple[dict, dict]:
    plan = _load(PLAN_PATH)
    smoke = _smoke_result()
    strict_inputs = _strict_inputs()
    calls: list[dict] = []

    def strict_verify(**kwargs):
        calls.append(kwargs)
        assert kwargs == {"result": smoke, **strict_inputs}
        return copy.deepcopy(smoke)

    monkeypatch.setattr(
        "paper_eval.s4_qualification_activation.verify_s4_remap_smoke_result",
        strict_verify,
    )
    artifact = build_s4_qualification_activation(
        qualification_plan=plan,
        qualification_plan_file_sha256=sha256_file(PLAN_PATH),
        smoke_result=smoke,
        smoke_result_file_sha256="5" * 64,
        source_sha256={"activation": "6" * 64, "test": "7" * 64},
        git_commit="deadbeef",
        **strict_inputs,
    )
    assert len(calls) == 1
    return artifact, plan


def test_activation_binds_verified_smoke_and_sealed_plan_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_before = PLAN_PATH.read_bytes()
    artifact, plan = _build(monkeypatch)
    verified = verify_s4_qualification_activation(artifact)
    payload = verified["payload"]

    assert PLAN_PATH.read_bytes() == plan_before
    assert payload["qualification_plan"] == {
        "file_sha256": sha256_file(PLAN_PATH),
        "plan_sha256": plan["plan_sha256"],
        "common_method_policy_sha256": plan["common_method_policy_sha256"],
    }
    assert payload["verified_smoke"] == {
        "kind": "S4_D0_REMAP_SMOKE_V2",
        "file_sha256": "5" * 64,
        "payload_sha256": _smoke_result()["payload_sha256"],
        "run_id": "s4-d0-remap-smoke-result-20260815-005",
        "verdict": "PASS",
        "history_id": "07741c45",
    }
    assert payload["activated_projection"] == {
        "reused_smoke_history_id": "07741c45",
        "live_history_ids": ["b6019101", "6071bd76", "a2f3aa27"],
        "live_blocks_sha256": payload_sha256(plan["blocks"][1:]),
        "sequential_blocks": True,
        "next_block_requires_prior_pass": True,
    }
    assert payload["authority"] == {
        "qualification_live_authorized": True,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def test_activation_requires_the_strict_remap_result_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(**_kwargs):
        raise ValueError("strict remap evidence failed")

    monkeypatch.setattr(
        "paper_eval.s4_qualification_activation.verify_s4_remap_smoke_result",
        reject,
    )
    with pytest.raises(ValueError, match="strict remap evidence failed"):
        build_s4_qualification_activation(
            qualification_plan=_load(PLAN_PATH),
            qualification_plan_file_sha256=sha256_file(PLAN_PATH),
            smoke_result=_smoke_result(),
            smoke_result_file_sha256="5" * 64,
            source_sha256={"activation": "6" * 64, "test": "7" * 64},
            git_commit="deadbeef",
            **_strict_inputs(),
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["payload"]["authority"].update(s5_authorized=True),
        lambda value: value["payload"]["authority"].update(
            pilot_execution_authorized=True
        ),
        lambda value: value["payload"]["activated_projection"].update(
            live_history_ids=["wrong"]
        ),
        lambda value: value["payload"].update(raw_response="private"),
        lambda value: value["payload"].update(extra="drift"),
        lambda value: value.update(run_id="wrong"),
    ],
)
def test_activation_tamper_or_authority_widening_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    mutate,
) -> None:
    artifact, _ = _build(monkeypatch)
    mutate(artifact)
    artifact = finalize_envelope(
        payload=artifact["payload"],
        protocol_version=artifact["protocol_version"],
        git_commit=artifact["git_commit"],
        run_id=artifact["run_id"],
    )

    with pytest.raises(ValueError):
        verify_s4_qualification_activation(artifact)


def test_activation_rejects_plan_or_external_hash_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _load(PLAN_PATH)
    plan["history_ids"][1] = "wrong"
    with pytest.raises(ValueError):
        build_s4_qualification_activation(
            qualification_plan=plan,
            qualification_plan_file_sha256=sha256_file(PLAN_PATH),
            smoke_result=_smoke_result(),
            smoke_result_file_sha256="5" * 64,
            source_sha256={"activation": "6" * 64, "test": "7" * 64},
            git_commit="deadbeef",
            **_strict_inputs(),
        )

    with pytest.raises(ValueError, match="qualification plan file"):
        build_s4_qualification_activation(
            qualification_plan=_load(PLAN_PATH),
            qualification_plan_file_sha256="missing",
            smoke_result=_smoke_result(),
            smoke_result_file_sha256="5" * 64,
            source_sha256={"activation": "6" * 64, "test": "7" * 64},
            git_commit="deadbeef",
            **_strict_inputs(),
        )


def test_activation_finalization_is_exclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, _ = _build(monkeypatch)
    output = tmp_path / "S4_QUALIFICATION_ACTIVATION_OVERLAY.json"

    assert finalize_s4_qualification_activation(path=output, artifact=artifact) == artifact
    with pytest.raises(FileExistsError):
        finalize_s4_qualification_activation(path=output, artifact=artifact)
