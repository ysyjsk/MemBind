"""SiliconFlow-ready V7 live-runner contract.

The runner is deliberately conservative: dry-run is the default, a sealed
method selection is mandatory for any live call, and the API key is read only
from an environment variable.  It is a transport/orchestration shell for the
future GPU campaign, not an unapproved V7 treatment implementation.
"""

from __future__ import annotations

import inspect
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping


class V7LiveRunnerError(RuntimeError):
    pass


SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
SILICONFLOW_AUTHORITY = "siliconflow-openai-compatible-v1"
SILICONFLOW_CONSTRUCTION_MODEL = "Qwen/Qwen3-32B"
SILICONFLOW_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
SILICONFLOW_EMBEDDING_DIMENSION = 1024
SILICONFLOW_HTTP_TIMEOUT_SECONDS = 900.0
V7_METHODS = frozenset({"OBSERVER_ONLY", "M0", "M1", "M2", "NULL"})
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True, slots=True)
class V7ProviderLane:
    authority: str
    base_url: str
    model: str
    api_key_env: str
    dimension: int | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and bool(value)
            for value in (self.authority, self.base_url, self.model, self.api_key_env)
        ):
            raise V7LiveRunnerError("V7 provider lane identity is incomplete")
        if not self.base_url.startswith(("https://", "http://")):
            raise V7LiveRunnerError("V7 provider lane base URL is invalid")
        if _ENV_NAME.fullmatch(self.api_key_env) is None:
            raise V7LiveRunnerError("V7 provider lane key environment is invalid")
        if self.dimension is not None and (
            isinstance(self.dimension, bool)
            or not isinstance(self.dimension, int)
            or self.dimension <= 0
        ):
            raise V7LiveRunnerError("V7 provider lane dimension is invalid")


@dataclass(frozen=True, slots=True)
class V7ProviderProfile:
    identity_kind: str
    construction: V7ProviderLane
    embedding: V7ProviderLane

    def __post_init__(self) -> None:
        if not isinstance(self.identity_kind, str) or not self.identity_kind:
            raise V7LiveRunnerError("V7 provider profile identity kind is invalid")
        if not isinstance(self.construction, V7ProviderLane) or not isinstance(
            self.embedding, V7ProviderLane
        ):
            raise V7LiveRunnerError("V7 provider profile lanes are invalid")
        if self.construction.dimension is not None or self.embedding.dimension is None:
            raise V7LiveRunnerError("V7 provider profile lane roles are invalid")


DEFAULT_V7_PROVIDER_PROFILE = V7ProviderProfile(
    identity_kind="SINGLE_PROVIDER_OPENAI_COMPATIBLE_FORMAL",
    construction=V7ProviderLane(
        authority=SILICONFLOW_AUTHORITY,
        base_url=SILICONFLOW_BASE_URL,
        model=SILICONFLOW_CONSTRUCTION_MODEL,
        api_key_env="SILICONFLOW_API_KEY",
    ),
    embedding=V7ProviderLane(
        authority=SILICONFLOW_AUTHORITY,
        base_url=SILICONFLOW_BASE_URL,
        model=SILICONFLOW_EMBEDDING_MODEL,
        api_key_env="SILICONFLOW_API_KEY",
        dimension=SILICONFLOW_EMBEDDING_DIMENSION,
    ),
)


@dataclass(frozen=True, slots=True)
class V7LiveConfig:
    output_root: Path
    run_id: str
    method: str = "OBSERVER_ONLY"
    dry_run: bool = True
    gate_path: Path | None = None
    provider_profile: V7ProviderProfile = DEFAULT_V7_PROVIDER_PROFILE
    source_count: int = 2

    def __post_init__(self) -> None:
        if not re.fullmatch(r"v7-[a-z0-9][a-z0-9-]{2,79}", self.run_id):
            raise V7LiveRunnerError("V7 run_id is invalid")
        if self.method not in V7_METHODS:
            raise V7LiveRunnerError("V7 method is not recognized")
        if isinstance(self.source_count, bool) or not isinstance(self.source_count, int) or self.source_count <= 0:
            raise V7LiveRunnerError("source_count must be positive")
        if not self.dry_run and self.method in {"OBSERVER_ONLY", "NULL"}:
            raise V7LiveRunnerError("live run requires a selected M0, M1 or M2 method")
        if not isinstance(self.provider_profile, V7ProviderProfile):
            raise V7LiveRunnerError("V7 provider profile is invalid")
        if not self.dry_run and self.source_count != 2:
            raise V7LiveRunnerError("live execution envelope differs from the gated two-source profile")

    @property
    def construction_base_url(self) -> str:
        return self.provider_profile.construction.base_url

    @property
    def embedding_base_url(self) -> str:
        return self.provider_profile.embedding.base_url

    @property
    def construction_model(self) -> str:
        return self.provider_profile.construction.model

    @property
    def embedding_model(self) -> str:
        return self.provider_profile.embedding.model

    @property
    def api_key_env(self) -> str | None:
        construction = self.provider_profile.construction.api_key_env
        embedding = self.provider_profile.embedding.api_key_env
        return construction if construction == embedding else None


def _lane_key(lane: V7ProviderLane) -> str | None:
    value = os.environ.get(lane.api_key_env)
    return value if value else None


def _provider_profile_projection(
    profile: V7ProviderProfile, *, include_key_presence: bool
) -> dict[str, Any]:
    def lane(value: V7ProviderLane) -> dict[str, Any]:
        result = {
            "authority": value.authority,
            "base_url": value.base_url,
            "model": value.model,
            "api_key_env": value.api_key_env,
        }
        if value.dimension is not None:
            result["dimension"] = value.dimension
        if include_key_presence:
            result["api_key_present"] = _lane_key(value) is not None
        return result

    return {
        "identity_kind": profile.identity_kind,
        "construction": lane(profile.construction),
        "embedding": lane(profile.embedding),
    }


def redact_config(config: V7LiveConfig) -> dict[str, Any]:
    """Return a manifest-safe config projection; never include secret bytes."""

    return {
        "run_id": config.run_id,
        "method": config.method,
        "dry_run": config.dry_run,
        "api_key_present": all(
            _lane_key(lane) is not None
            for lane in (
                config.provider_profile.construction,
                config.provider_profile.embedding,
            )
        ),
        "provider_profile": _provider_profile_projection(
            config.provider_profile,
            include_key_presence=True,
        ),
        "construction_base_url": config.construction_base_url,
        "embedding_base_url": config.embedding_base_url,
        "construction_model": config.construction_model,
        "embedding_model": config.embedding_model,
        "source_count": config.source_count,
    }


def _read_gate(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise V7LiveRunnerError("method selection seal is missing") from exc
    except json.JSONDecodeError as exc:
        raise V7LiveRunnerError("method selection seal is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise V7LiveRunnerError("method selection seal must be an object")
    return value


def _lane_matches(frozen: Mapping[str, Any], lane: V7ProviderLane) -> bool:
    frozen_model = frozen.get("model", frozen.get("served_model_id"))
    return (
        frozen.get("authority") == lane.authority
        and frozen.get("base_url") == lane.base_url
        and frozen_model == lane.model
        and (
            lane.dimension is None
            or frozen.get("dimension") == lane.dimension
        )
    )


def validate_provider_profile_binding(
    profile: V7ProviderProfile,
    campaign_identity: Mapping[str, Any],
) -> None:
    """Require the requested live provider profile to match formal Gate evidence."""

    if not isinstance(profile, V7ProviderProfile) or not isinstance(
        campaign_identity, Mapping
    ):
        raise V7LiveRunnerError("formal campaign provider profile is invalid")
    construction = campaign_identity.get("construction")
    embedding = campaign_identity.get("embedding")
    if isinstance(construction, Mapping) and isinstance(embedding, Mapping):
        valid = (
            campaign_identity.get("provider_identity_kind") == profile.identity_kind
            and _lane_matches(construction, profile.construction)
            and _lane_matches(embedding, profile.embedding)
        )
    else:
        # The original formal protocol recorded only model IDs. It is accepted
        # exclusively for the frozen legacy default profile.
        provider = campaign_identity.get("provider")
        valid = (
            profile == DEFAULT_V7_PROVIDER_PROFILE
            and isinstance(provider, Mapping)
            and provider.get("construction_model") == profile.construction.model
            and provider.get("embedding_model") == profile.embedding.model
        )
    if not valid:
        raise V7LiveRunnerError(
            "formal campaign provider profile does not match live configuration"
        )


def validate_live_gate(config: V7LiveConfig) -> Mapping[str, Any] | None:
    if config.dry_run:
        return None
    if config.gate_path is None:
        raise V7LiveRunnerError("method selection seal is required before live run")
    if Path(config.gate_path).name == "DEVELOPMENT_METHOD_SELECTION.json":
        raise V7LiveRunnerError(
            "development method selection cannot authorize live treatment"
        )
    gate = _read_gate(Path(config.gate_path))
    authorized = gate.get("authorized") is True
    selected = gate.get("selected_method") or gate.get("method") or gate.get("winner")
    if (
        gate.get("status") != "AUTHORIZED"
        or not authorized
        or gate.get("treatment_authorized") is not True
        or selected != config.method
    ):
        raise V7LiveRunnerError("method selection seal does not authorize the requested method")
    if Path(config.gate_path).name != "METHOD_SELECTION.json":
        raise V7LiveRunnerError("method selection must be a hash-sealed METHOD_SELECTION.json")
    try:
        from .gates import evaluate_opportunity_gates
        from .observer_campaign import verify_observer_manifest

        verification = verify_observer_manifest(Path(config.gate_path).parent)
        decision = _read_gate(Path(config.gate_path).parent / "R3_DECISION_INPUT.json")
        recomputed = evaluate_opportunity_gates(decision)
    except Exception as exc:
        raise V7LiveRunnerError("method selection is not hash-sealed R3 evidence") from exc
    identity = verification.get("campaign_identity")
    workload = identity.get("workload") if isinstance(identity, Mapping) else None
    region = (
        identity.get("selected_characterization_region")
        if isinstance(identity, Mapping)
        else None
    )
    harness = identity.get("observer_harness") if isinstance(identity, Mapping) else None
    r12 = workload.get("r1_r2") if isinstance(workload, Mapping) else None
    r3 = workload.get("r3_blocks") if isinstance(workload, Mapping) else None
    try:
        validate_provider_profile_binding(config.provider_profile, identity)
        provider_profile_valid = True
    except V7LiveRunnerError:
        provider_profile_valid = False
    campaign_identity_valid = (
        isinstance(identity, Mapping)
        and identity.get("schema_version")
        in {
            "membind.v7.real-observer-campaign-identity.v1",
            "membind.v7.real-observer-campaign-identity.v2",
        }
        and identity.get("treatment_calls") == 0
        and identity.get("response_replay_calls") == 0
        and isinstance(identity.get("protocol_sha256"), str)
        and _DIGEST.fullmatch(str(identity.get("protocol_sha256"))) is not None
        and provider_profile_valid
        and isinstance(r12, Mapping)
        and r12.get("source_count") == 2
        and isinstance(r3, list)
        and len(r3) == 2
        and all(isinstance(row, Mapping) and row.get("source_count") == 6 for row in r3)
        and isinstance(region, Mapping)
        and region.get("operator") == gate.get("selected_operator")
        and region.get("seam") == gate.get("selected_seam")
        and isinstance(harness, Mapping)
        and harness.get("schema_version")
        == "membind.v7.observer-harness-verification.v1"
        and harness.get("status") == "PASS"
        and isinstance(harness.get("source_sha256"), Mapping)
        and bool(harness.get("source_sha256"))
    )
    if (
        verification.get("evidence_manifest_sha256") is None
        or dict(gate) != recomputed
        or not campaign_identity_valid
    ):
        raise V7LiveRunnerError("method selection is not hash-sealed R3 evidence")
    return gate


def _build_provider_client(lane: V7ProviderLane) -> Any:
    key = _lane_key(lane)
    if key is None:
        raise V7LiveRunnerError(
            f"{lane.api_key_env} is required for a live provider call"
        )
    try:
        import httpx
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise V7LiveRunnerError(
            "openai package is required for an OpenAI-compatible V7 live run"
        ) from exc
    timeout = httpx.Timeout(
        connect=10.0,
        read=SILICONFLOW_HTTP_TIMEOUT_SECONDS,
        write=SILICONFLOW_HTTP_TIMEOUT_SECONDS,
        pool=SILICONFLOW_HTTP_TIMEOUT_SECONDS,
    )
    http_client = httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)
    return AsyncOpenAI(
        api_key=key,
        base_url=lane.base_url,
        timeout=timeout,
        max_retries=0,
        http_client=http_client,
    )


def build_construction_client(config: V7LiveConfig) -> Any:
    """Build the configured construction client without logging credentials."""

    return _build_provider_client(config.provider_profile.construction)


def build_embedding_client(config: V7LiveConfig) -> Any:
    """Build the configured embedding client without logging credentials."""

    return _build_provider_client(config.provider_profile.embedding)


def build_siliconflow_client(config: V7LiveConfig) -> Any:
    """Compatibility alias for the configured construction lane."""

    return build_construction_client(config)


def build_siliconflow_embedding_client(config: V7LiveConfig) -> Any:
    """Compatibility alias for the configured embedding lane."""

    return build_embedding_client(config)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _write_new(path: Path, value: Mapping[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    payload = (
        json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
            default=str,
        ).encode("ascii")
        + b"\n"
    )
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    except FileExistsError as exc:
        raise V7LiveRunnerError(f"artifact already exists: {path}") from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("V7 live artifact write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_adapter_result(value: Any, config: V7LiveConfig) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V7LiveRunnerError("live adapter result must be an object")
    integers = {
        name: value.get(name)
        for name in (
            "provider_calls",
            "treatment_calls",
            "native_publication_calls",
            "false_reuse_count",
        )
    }
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in integers.values()):
        raise V7LiveRunnerError("live adapter result accounting is invalid")
    expected_sequences = list(range(config.source_count))
    if (
        value.get("schema_version") != "membind.v7.live-adapter-result.v1"
        or value.get("status") != "COMPLETED"
        or value.get("run_id") != config.run_id
        or value.get("method") != config.method
        or value.get("source_count") != config.source_count
        or integers["treatment_calls"] <= 0
        or integers["native_publication_calls"] != config.source_count
        or value.get("publication_source_sequences") != expected_sequences
        or value.get("canonical_equivalent") is not True
        or integers["false_reuse_count"] != 0
        or not isinstance(value.get("artifact_manifest_sha256"), str)
        or _DIGEST.fullmatch(str(value.get("artifact_manifest_sha256"))) is None
    ):
        raise V7LiveRunnerError("live adapter result fails the gated execution contract")
    return {
        "schema_version": value["schema_version"],
        "status": value["status"],
        "run_id": value["run_id"],
        "method": value["method"],
        "source_count": value["source_count"],
        **integers,
        "publication_source_sequences": expected_sequences,
        "canonical_equivalent": True,
        "artifact_manifest_sha256": value["artifact_manifest_sha256"],
    }


def verify_v7_live_artifacts(root: str | Path) -> dict[str, Any]:
    target = Path(root)
    try:
        manifest = json.loads((target / "MANIFEST.json").read_text(encoding="ascii"))
        seal = json.loads((target / "SEAL.json").read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise V7LiveRunnerError("V7 live artifact seal is unreadable") from None
    if (
        not isinstance(manifest, Mapping)
        or not isinstance(seal, Mapping)
        or manifest.get("schema_version") != "membind.v7.live-manifest.v1"
        or seal.get("schema_version") != "membind.v7.live-seal.v1"
        or seal.get("manifest_sha256") != _sha256(target / "MANIFEST.json")
    ):
        raise V7LiveRunnerError("V7 live artifact seal is invalid")
    members = manifest.get("files")
    if not isinstance(members, list) or not members:
        raise V7LiveRunnerError("V7 live artifact manifest is empty")
    expected = {"MANIFEST.json", "SEAL.json"}
    for member in members:
        if not isinstance(member, Mapping):
            raise V7LiveRunnerError("V7 live artifact member is invalid")
        name = member.get("path")
        digest = member.get("sha256")
        if not isinstance(name, str) or Path(name).name != name or not isinstance(digest, str):
            raise V7LiveRunnerError("V7 live artifact member identity is invalid")
        path = target / name
        if not path.is_file() or _sha256(path) != digest:
            raise V7LiveRunnerError("V7 live artifact digest mismatch")
        expected.add(name)
    if {path.name for path in target.glob("*.json")} != expected:
        raise V7LiveRunnerError("V7 live artifact inventory differs from its seal")
    return {"status": "PASS", "manifest_sha256": _sha256(target / "MANIFEST.json")}


async def run_v7_live_async(
    config: V7LiveConfig,
    *,
    provider_call: Callable[[], Awaitable[Any] | Any] | None = None,
) -> dict[str, Any]:
    """Run a dry-run or one explicitly authorized provider callback.

    The callback is dependency-injected so GPU integration can bind the
    existing Graphiti native runner after the method-selection gate. This
    function itself never skips native demand construction or publishes state.
    """

    gate = validate_live_gate(config)
    if not config.dry_run:
        missing_key_environments = sorted(
            {
                lane.api_key_env
                for lane in (
                    config.provider_profile.construction,
                    config.provider_profile.embedding,
                )
                if _lane_key(lane) is None
            }
        )
        if missing_key_environments:
            raise V7LiveRunnerError(
                "required live provider key environment is missing: "
                + ", ".join(missing_key_environments)
            )
        if provider_call is None:
            raise V7LiveRunnerError("live runner requires an injected provider/native callback")
    root = Path(config.output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise V7LiveRunnerError("V7 output root must be fresh")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    status = "DRY_RUN"
    provider_calls = 0
    treatment_calls = 0
    native_publication_calls = 0
    adapter_invocations = 0
    adapter_result: dict[str, Any] | None = None
    if not config.dry_run:
        status = "LIVE_AUTHORIZED"
        try:
            adapter_invocations = 1
            raw_result = await _maybe_await(provider_call())
            adapter_result = _validate_adapter_result(raw_result, config)
            provider_calls = int(adapter_result["provider_calls"])
            treatment_calls = int(adapter_result["treatment_calls"])
            native_publication_calls = int(adapter_result["native_publication_calls"])
        except BaseException as exc:
            _write_new(
                root / "RUN_FAILURE.json",
                {
                    "schema_version": "membind.v7.live-failure.v2",
                    "status": "FAILED_CLOSED",
                    "adapter_invocations": adapter_invocations,
                    "provider_calls": None,
                    "treatment_calls": None,
                    "native_publication_calls": None,
                    "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "error_message_digest": hashlib.sha256(str(exc).encode("utf-8", errors="backslashreplace")).hexdigest(),
                    "provider_key_recorded": False,
                    "raw_adapter_result_recorded": False,
                },
                mode=0o600,
            )
            raise
    gate_sha256 = None if gate is None else _sha256(Path(config.gate_path))
    manifest = {
        "schema_version": "membind.v7.live-runner-manifest.v2",
        "status": status,
        "runner_source_sha256": _sha256(Path(__file__).resolve()),
        "provider_profile": _provider_profile_projection(
            config.provider_profile,
            include_key_presence=False,
        ),
        "method": config.method,
        "adapter_invocations": adapter_invocations,
        "provider_calls": provider_calls,
        "treatment_calls": treatment_calls,
        "native_publication_calls": native_publication_calls,
        "treatment_authorized": not config.dry_run,
        "gate_present": gate is not None,
        "gate_sha256": gate_sha256,
        "config": redact_config(config),
        "adapter_result": adapter_result,
    }
    _write_new(root / "RUN_MANIFEST.json", manifest)
    _write_new(
        root / "RUN_STATE.json",
        {
            "schema_version": "membind.v7.live-run-state.v2",
            "status": status,
            "provider_calls": provider_calls,
            "treatment_calls": treatment_calls,
            "native_publication_calls": native_publication_calls,
            "next_action": (
                "await a hash-sealed authorized method selection"
                if config.dry_run
                else "inspect the sealed two-source differential"
            ),
        },
    )
    members = [
        {"path": name, "sha256": _sha256(root / name)}
        for name in ("RUN_MANIFEST.json", "RUN_STATE.json")
    ]
    _write_new(
        root / "MANIFEST.json",
        {
            "schema_version": "membind.v7.live-manifest.v1",
            "status": "SEALED",
            "run_id": config.run_id,
            "method": config.method,
            "files": members,
        },
    )
    _write_new(
        root / "SEAL.json",
        {
            "schema_version": "membind.v7.live-seal.v1",
            "status": "SEALED",
            "manifest_sha256": _sha256(root / "MANIFEST.json"),
            "treatment_authorized": not config.dry_run,
        },
    )
    verify_v7_live_artifacts(root)
    return manifest


def run_v7_live(config: V7LiveConfig, *, provider_call: Callable[[], Awaitable[Any] | Any] | None = None) -> dict[str, Any]:
    import asyncio

    return asyncio.run(run_v7_live_async(config, provider_call=provider_call))


__all__ = [
    "DEFAULT_V7_PROVIDER_PROFILE",
    "SILICONFLOW_BASE_URL",
    "SILICONFLOW_CONSTRUCTION_MODEL",
    "SILICONFLOW_EMBEDDING_MODEL",
    "V7LiveConfig",
    "V7LiveRunnerError",
    "V7ProviderLane",
    "V7ProviderProfile",
    "build_construction_client",
    "build_embedding_client",
    "build_siliconflow_client",
    "build_siliconflow_embedding_client",
    "redact_config",
    "run_v7_live",
    "run_v7_live_async",
    "validate_live_gate",
    "validate_provider_profile_binding",
    "verify_v7_live_artifacts",
]
