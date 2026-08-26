from __future__ import annotations

import asyncio
import stat
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import (
    V7LiveConfig,
    V7LiveRunnerError,
    V7ProviderLane,
    V7ProviderProfile,
    redact_config,
    run_v7_live_async,
    validate_live_gate,
    validate_provider_profile_binding,
)


PROJECT = Path(__file__).resolve().parents[1]
DEVELOPMENT_SELECTION = (
    PROJECT
    / "v7/artifacts/v7-development-strict-20260826-002/DEVELOPMENT_METHOD_SELECTION.json"
)


def _profile() -> V7ProviderProfile:
    return V7ProviderProfile(
        identity_kind="COMPOSITE_OPENAI_COMPATIBLE_FORMAL",
        construction=V7ProviderLane(
            authority="construction-authority-v1",
            base_url="https://construction.example/v1",
            model="construction-model-v1",
            api_key_env="CONSTRUCTION_API_KEY",
        ),
        embedding=V7ProviderLane(
            authority="embedding-authority-v1",
            base_url="https://embedding.example/v1",
            model="embedding-model-v1",
            api_key_env="EMBEDDING_API_KEY",
            dimension=1024,
        ),
    )


def test_provider_profile_is_lane_separated_and_manifest_safe(monkeypatch) -> None:
    monkeypatch.setenv("CONSTRUCTION_API_KEY", "construction-secret")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-secret")
    config = V7LiveConfig(
        output_root=Path("unused"),
        run_id="v7-profile-dry",
        provider_profile=_profile(),
    )

    redacted = redact_config(config)
    assert redacted["provider_profile"]["construction"]["api_key_present"] is True
    assert redacted["provider_profile"]["embedding"]["api_key_present"] is True
    assert redacted["provider_profile"]["construction"]["model"] == (
        "construction-model-v1"
    )
    assert "construction-secret" not in repr(redacted)
    assert "embedding-secret" not in repr(redacted)


def test_formal_campaign_identity_must_match_explicit_provider_profile() -> None:
    profile = _profile()
    identity = {
        "provider_identity_kind": profile.identity_kind,
        "construction": {
            "authority": profile.construction.authority,
            "base_url": profile.construction.base_url,
            "model": profile.construction.model,
        },
        "embedding": {
            "authority": profile.embedding.authority,
            "base_url": profile.embedding.base_url,
            "model": profile.embedding.model,
            "dimension": profile.embedding.dimension,
        },
    }
    validate_provider_profile_binding(profile, identity)

    identity["construction"] = {
        **identity["construction"],
        "model": "different-model",
    }
    with pytest.raises(V7LiveRunnerError, match="provider profile"):
        validate_provider_profile_binding(profile, identity)


def test_development_method_selection_can_never_authorize_live(tmp_path: Path) -> None:
    config = V7LiveConfig(
        output_root=tmp_path / "run",
        run_id="v7-development-null",
        method="M1",
        dry_run=False,
        gate_path=DEVELOPMENT_SELECTION,
    )
    with pytest.raises(V7LiveRunnerError, match="development method selection"):
        validate_live_gate(config)


def test_provider_independent_dry_run_uses_private_artifact_permissions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dry"
    config = V7LiveConfig(
        output_root=root,
        run_id="v7-provider-profile-dry",
        provider_profile=_profile(),
    )
    result = asyncio.run(run_v7_live_async(config))

    assert result["status"] == "DRY_RUN"
    assert result["provider_profile"]["identity_kind"] == (
        "COMPOSITE_OPENAI_COMPATIBLE_FORMAL"
    )
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in root.glob("*.json")
    )
