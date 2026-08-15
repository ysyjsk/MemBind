"""TDD for fixed-four activation after a strict sidecar smoke PASS."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval import PROTOCOL_VERSION
from paper_eval.artifacts import finalize_envelope, payload_sha256, sha256_file
from paper_eval.s4_sidecar_qualification_activation import (
    RUN_ID,
    SCHEMA,
    build_s4_sidecar_qualification_activation,
    finalize_s4_sidecar_qualification_activation,
    verify_s4_sidecar_qualification_activation,
    verify_s4_sidecar_qualification_activation_external,
)


PROJECT = Path(__file__).resolve().parents[1]
PLAN_PATH = PROJECT / "artifacts/paper_eval/native/S4_D0_QUALIFICATION_PLAN.json"
SOURCE_PATHS = {
    "activation": PROJECT
    / "src/paper_eval/s4_sidecar_qualification_activation.py",
    "finalizer": PROJECT
    / "scripts/finalize_s4_sidecar_qualification_activation.py",
    "smoke_result_verifier": PROJECT
    / "src/paper_eval/s4_sidecar_smoke_result_verifier.py",
    "test": PROJECT / "tests/test_s4_sidecar_qualification_activation.py",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _smoke_result() -> dict:
    return finalize_envelope(
        payload={
            "schema_version": "membind.paper-eval-v3.s4-d0-sidecar-smoke-result.v3",
            "stage": "S4",
            "verdict": "PASS",
            "authority_file_sha256": "1" * 64,
            "authority_consumption_file_sha256": "2" * 64,
            "capture_result_file_sha256": "3" * 64,
            "replay_result_file_sha256": "4" * 64,
            "candidate_sidecar_file_sha256": "5" * 64,
            "evaluation": {"verdict": "PASS"},
            "authority": {
                "s4_four_history_qualification_authorized": True,
                "s5_authorized": False,
                "pilot_execution_authorized": False,
            },
        },
        protocol_version=PROTOCOL_VERSION,
        git_commit="deadbeef",
        run_id="s4-d0-sidecar-smoke-result-20260815-008",
    )


def _phase(mode: str) -> dict:
    capture = mode == "capture"
    run_id = (
        "s4-d0-capture-20260815-008"
        if capture
        else "s4-d0-replay-20260815-008"
    )
    return finalize_envelope(
        payload={
            "run_id": run_id,
            "cache_evidence": {
                "prompt_cache_sha256": "a" * 64,
                "embedding_cache_sha256": "c" * 64,
                "candidate_sidecar_sha256": "5" * 64,
            },
            "checkpoint_sha256": ("e" if capture else "f") * 64,
            "events_sha256": ("6" if capture else "9") * 64,
        },
        protocol_version=PROTOCOL_VERSION,
        git_commit="deadbeef",
        run_id=run_id,
    )


def _strict_inputs() -> dict:
    return {
        "authority": finalize_envelope(
            payload={"sentinel": "authority"},
            protocol_version=PROTOCOL_VERSION,
            git_commit="deadbeef",
            run_id="s4-sidecar-smoke-authority-20260815-008",
        ),
        "authority_file_sha256": "1" * 64,
        "consumption": finalize_envelope(
            payload={"sentinel": "consumption"},
            protocol_version=PROTOCOL_VERSION,
            git_commit="deadbeef",
            run_id="s4-sidecar-authority-consumption-20260815-008",
        ),
        "consumption_file_sha256": "2" * 64,
        "capture_result": _phase("capture"),
        "capture_result_file_sha256": "3" * 64,
        "replay_result": _phase("replay"),
        "replay_result_file_sha256": "4" * 64,
        "candidate_sidecar_file_sha256": "5" * 64,
    }


def _source_sha256() -> dict[str, str]:
    return {name: sha256_file(path) for name, path in SOURCE_PATHS.items()}


def _build(monkeypatch: pytest.MonkeyPatch) -> tuple[dict, dict]:
    plan = _load(PLAN_PATH)
    smoke = _smoke_result()
    strict_inputs = _strict_inputs()
    calls: list[dict] = []

    def strict_verify(**kwargs):
        calls.append(kwargs)
        assert kwargs == {
            "result": smoke,
            "expected_attempt": "008",
            **strict_inputs,
        }
        return copy.deepcopy(smoke)

    monkeypatch.setattr(
        "paper_eval.s4_sidecar_qualification_activation.verify_s4_sidecar_smoke_result",
        strict_verify,
    )
    artifact = build_s4_sidecar_qualification_activation(
        qualification_plan=plan,
        qualification_plan_file_sha256=sha256_file(PLAN_PATH),
        smoke_result=smoke,
        smoke_result_file_sha256="6" * 64,
        source_sha256=_source_sha256(),
        git_commit="deadbeef",
        **strict_inputs,
    )
    assert len(calls) == 1
    return artifact, plan


def test_activation_binds_sidecar_pass_and_reuses_only_smoke_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = PLAN_PATH.read_bytes()
    artifact, plan = _build(monkeypatch)
    payload = verify_s4_sidecar_qualification_activation(artifact)["payload"]

    assert PLAN_PATH.read_bytes() == before
    assert SCHEMA == "membind.paper-eval-v3.s4-sidecar-qualification-activation.v3"
    assert RUN_ID == "s4-sidecar-qualification-activation-20260815-003"
    assert artifact["run_id"] == RUN_ID
    assert payload["qualification_plan"]["plan_sha256"] == plan["plan_sha256"]
    assert payload["verified_smoke"] == {
        "kind": "S4_D0_BILATERAL_SIDECAR_SMOKE_V3",
        "file_sha256": "6" * 64,
        "payload_sha256": _smoke_result()["payload_sha256"],
        "run_id": "s4-d0-sidecar-smoke-result-20260815-008",
        "verdict": "PASS",
        "history_id": "07741c45",
        "evidence": {
            "authority": {
                "file_sha256": "1" * 64,
                "payload_sha256": _strict_inputs()["authority"]["payload_sha256"],
                "run_id": "s4-sidecar-smoke-authority-20260815-008",
            },
            "consumption": {
                "file_sha256": "2" * 64,
                "payload_sha256": _strict_inputs()["consumption"][
                    "payload_sha256"
                ],
                "run_id": (
                    "s4-sidecar-authority-consumption-20260815-008"
                ),
            },
            "candidate_sidecar_file_sha256": "5" * 64,
            "phases": {
                "U0_CAPTURE": {
                    "file_sha256": "3" * 64,
                    "payload_sha256": _phase("capture")["payload_sha256"],
                    "run_id": "s4-d0-capture-20260815-008",
                    "prompt_cache_sha256": "a" * 64,
                    "embedding_cache_sha256": "c" * 64,
                    "candidate_sidecar_sha256": "5" * 64,
                    "checkpoint_sha256": "e" * 64,
                    "events_sha256": "6" * 64,
                },
                "D0_READ_ONLY_REPLAY": {
                    "file_sha256": "4" * 64,
                    "payload_sha256": _phase("replay")["payload_sha256"],
                    "run_id": "s4-d0-replay-20260815-008",
                    "prompt_cache_sha256": "a" * 64,
                    "embedding_cache_sha256": "c" * 64,
                    "candidate_sidecar_sha256": "5" * 64,
                    "checkpoint_sha256": "f" * 64,
                    "events_sha256": "9" * 64,
                },
            },
        },
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
    assert payload["source_sha256"] == dict(sorted(_source_sha256().items()))


def test_activation_requires_strict_sidecar_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(**_kwargs):
        raise ValueError("strict sidecar evidence failed")

    monkeypatch.setattr(
        "paper_eval.s4_sidecar_qualification_activation.verify_s4_sidecar_smoke_result",
        reject,
    )
    with pytest.raises(ValueError, match="strict sidecar evidence failed"):
        build_s4_sidecar_qualification_activation(
            qualification_plan=_load(PLAN_PATH),
            qualification_plan_file_sha256=sha256_file(PLAN_PATH),
            smoke_result=_smoke_result(),
            smoke_result_file_sha256="6" * 64,
            source_sha256=_source_sha256(),
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
        lambda value: value.update(run_id="wrong"),
    ],
)
def test_activation_tamper_fails_closed(
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
        verify_s4_sidecar_qualification_activation(artifact)


def test_activation_finalizer_is_exclusive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact, _ = _build(monkeypatch)
    target = tmp_path / "activation.json"

    assert finalize_s4_sidecar_qualification_activation(
        path=target, artifact=artifact
    ) == artifact
    with pytest.raises(FileExistsError):
        finalize_s4_sidecar_qualification_activation(path=target, artifact=artifact)


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def _disk_backed_activation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[dict, dict[str, Path]]:
    strict_inputs = _strict_inputs()
    smoke = _smoke_result()
    paths = {
        "smoke_result_path": tmp_path / "smoke.json",
        "authority_path": tmp_path / "authority.json",
        "consumption_path": tmp_path / "consumption.json",
        "capture_result_path": tmp_path / "capture.json",
        "replay_result_path": tmp_path / "replay.json",
        "candidate_sidecar_path": tmp_path / "candidate-sidecar.jsonl",
        "prompt_cache_path": tmp_path / "prompt.jsonl",
        "embedding_cache_path": tmp_path / "embedding.jsonl",
        "capture_checkpoint_path": tmp_path / "capture-checkpoint.json",
        "capture_events_path": tmp_path / "capture-events.jsonl",
        "replay_checkpoint_path": tmp_path / "replay-checkpoint.json",
        "replay_events_path": tmp_path / "replay-events.jsonl",
    }
    external_contents = {
        "candidate_sidecar_path": "sealed sidecar\n",
        "prompt_cache_path": "sealed prompt cache\n",
        "embedding_cache_path": "sealed embedding cache\n",
        "capture_checkpoint_path": "sealed capture checkpoint\n",
        "capture_events_path": "sealed capture events\n",
        "replay_checkpoint_path": "sealed replay checkpoint\n",
        "replay_events_path": "sealed replay events\n",
    }
    for name, content in external_contents.items():
        paths[name].write_text(content, encoding="ascii")

    shared_cache_hashes = {
        "prompt_cache_sha256": sha256_file(paths["prompt_cache_path"]),
        "embedding_cache_sha256": sha256_file(paths["embedding_cache_path"]),
        "candidate_sidecar_sha256": sha256_file(paths["candidate_sidecar_path"]),
    }
    for input_name, checkpoint_name, events_name in (
        (
            "capture_result",
            "capture_checkpoint_path",
            "capture_events_path",
        ),
        (
            "replay_result",
            "replay_checkpoint_path",
            "replay_events_path",
        ),
    ):
        phase = strict_inputs[input_name]
        phase["payload"]["cache_evidence"] = shared_cache_hashes
        phase["payload"]["checkpoint_sha256"] = sha256_file(
            paths[checkpoint_name]
        )
        phase["payload"]["events_sha256"] = sha256_file(paths[events_name])
        strict_inputs[input_name] = finalize_envelope(
            payload=phase["payload"],
            protocol_version=phase["protocol_version"],
            git_commit=phase["git_commit"],
            run_id=phase["run_id"],
        )
    for path_name, input_name in (
        ("smoke_result_path", None),
        ("authority_path", "authority"),
        ("consumption_path", "consumption"),
        ("capture_result_path", "capture_result"),
        ("replay_result_path", "replay_result"),
    ):
        _write_json(
            paths[path_name], smoke if input_name is None else strict_inputs[input_name]
        )

    def strict_verify(**kwargs):
        assert kwargs["expected_attempt"] == "008"
        return copy.deepcopy(kwargs["result"])

    monkeypatch.setattr(
        "paper_eval.s4_sidecar_qualification_activation.verify_s4_sidecar_smoke_result",
        strict_verify,
    )
    artifact = build_s4_sidecar_qualification_activation(
        qualification_plan=_load(PLAN_PATH),
        qualification_plan_file_sha256=sha256_file(PLAN_PATH),
        smoke_result=smoke,
        smoke_result_file_sha256=sha256_file(paths["smoke_result_path"]),
        authority=strict_inputs["authority"],
        authority_file_sha256=sha256_file(paths["authority_path"]),
        consumption=strict_inputs["consumption"],
        consumption_file_sha256=sha256_file(paths["consumption_path"]),
        capture_result=strict_inputs["capture_result"],
        capture_result_file_sha256=sha256_file(paths["capture_result_path"]),
        replay_result=strict_inputs["replay_result"],
        replay_result_file_sha256=sha256_file(paths["replay_result_path"]),
        candidate_sidecar_file_sha256=sha256_file(
            paths["candidate_sidecar_path"]
        ),
        source_sha256=_source_sha256(),
        git_commit="deadbeef",
    )
    return artifact, paths


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["payload"]["activated_projection"].update(
            live_blocks_sha256="0" * 64
        ),
        lambda value: value["payload"]["source_sha256"].update(
            activation="0" * 64
        ),
    ],
)
def test_external_verifier_reopens_chain_and_rejects_refinalized_forgery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutate,
) -> None:
    artifact, paths = _disk_backed_activation(monkeypatch, tmp_path)
    assert verify_s4_sidecar_qualification_activation_external(
        value=artifact,
        qualification_plan_path=PLAN_PATH,
        source_paths=SOURCE_PATHS,
        **paths,
    ) == artifact

    forged = copy.deepcopy(artifact)
    mutate(forged)
    forged = finalize_envelope(
        payload=forged["payload"],
        protocol_version=forged["protocol_version"],
        git_commit=forged["git_commit"],
        run_id=forged["run_id"],
    )
    assert verify_s4_sidecar_qualification_activation(forged) == forged
    with pytest.raises(ValueError, match="external evidence drift"):
        verify_s4_sidecar_qualification_activation_external(
            value=forged,
            qualification_plan_path=PLAN_PATH,
            source_paths=SOURCE_PATHS,
            **paths,
        )


@pytest.mark.parametrize(
    "path_name",
    [
        "prompt_cache_path",
        "embedding_cache_path",
        "capture_checkpoint_path",
        "capture_events_path",
        "replay_checkpoint_path",
        "replay_events_path",
    ],
)
def test_external_verifier_rejects_smoke_evidence_file_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path_name: str,
) -> None:
    artifact, paths = _disk_backed_activation(monkeypatch, tmp_path)

    assert verify_s4_sidecar_qualification_activation_external(
        value=artifact,
        qualification_plan_path=PLAN_PATH,
        source_paths=SOURCE_PATHS,
        **paths,
    ) == artifact
    paths[path_name].write_text("replacement evidence\n", encoding="ascii")

    with pytest.raises(ValueError, match="external evidence drift"):
        verify_s4_sidecar_qualification_activation_external(
            value=artifact,
            qualification_plan_path=PLAN_PATH,
            source_paths=SOURCE_PATHS,
            **paths,
        )
