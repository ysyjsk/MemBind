"""S5 production-path composition for the Native A0/P* smoke lane.

This module is intentionally a thin composition layer.  It does not create a
model client, open Neo4j, or grant live authority.  A caller must provide the
already-authorized Graphiti object and the exact ``graphiti_native`` binding;
the runner then connects that binding to the offline-qualified scheduler and
the crash-consistent S5 attempt store.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256
from .s5_durable_attempt_store import S5AttemptStore
from .s5_graphiti_native_binding import (
    S5GraphitiNativeBinding,
    build_native_add_episode_callable,
)
from .s5_native_method_adapters import (
    A0,
    P_STAR,
    S5EpisodeRef,
    S5MethodSpec,
    run_a0,
    run_p_c2,
)


SCHEMA = "membind.paper-eval-v3.s5-production-identity.v1"
GRAPHITI_VERSION = "0.29.3"
GRAPHITI_COMMIT = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_METHODS = {A0, P_STAR, "M*"}
_PRIVATE_FIELDS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "episode",
    "group_id",
    "messages",
    "namespace",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
}
_IDENTITY_FIELDS = {
    "schema_version",
    "status",
    "method",
    "graphiti_version",
    "graphiti_commit",
    "graphiti_native_module",
    "graphiti_native_add_episode_qualname",
    "graphiti_episode_kwargs_qualname",
    "graphiti_native_source_sha256",
    "graphiti_semantic_api_sha256",
    "runtime_factory_entrypoint",
    "runtime_factory_source_sha256",
    "scheduler_source_sha256",
    "scheduler_test_source_sha256",
    "durable_store_source_sha256",
    "durable_store_test_source_sha256",
    "runtime_config_sha256",
    "fx0_parity_artifact_sha256",
    "method_policy",
    "qualification_status",
    "failure_policy",
    "identity_sha256",
}


class S5ProductionIdentityError(ValueError):
    """A production identity or runner composition failed closed."""


def _fail(code: str) -> S5ProductionIdentityError:
    return S5ProductionIdentityError(code)


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_identity_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def build_s5_production_identity(
    *,
    method: str,
    graphiti_version: str,
    graphiti_commit: str,
    graphiti_native_source_sha256: str,
    graphiti_semantic_api_sha256: str,
    runtime_factory_entrypoint: str,
    runtime_factory_source_sha256: str,
    scheduler_source_sha256: str,
    scheduler_test_source_sha256: str,
    durable_store_source_sha256: str,
    durable_store_test_source_sha256: str,
    runtime_config_sha256: str,
    fx0_parity_artifact_sha256: str | None = None,
) -> dict[str, object]:
    """Build a sealed, hash-only identity for one S5 method binding."""

    if method not in _METHODS:
        raise _fail("method_invalid")
    if graphiti_version != GRAPHITI_VERSION:
        raise _fail("graphiti_version_mismatch")
    if graphiti_commit != GRAPHITI_COMMIT:
        raise _fail("graphiti_commit_mismatch")
    for value, code in (
        (graphiti_native_source_sha256, "graphiti_native_source_invalid"),
        (graphiti_semantic_api_sha256, "graphiti_semantic_api_invalid"),
        (runtime_factory_source_sha256, "runtime_factory_source_invalid"),
        (scheduler_source_sha256, "scheduler_source_invalid"),
        (scheduler_test_source_sha256, "scheduler_test_source_invalid"),
        (durable_store_source_sha256, "durable_store_source_invalid"),
        (durable_store_test_source_sha256, "durable_store_test_source_invalid"),
        (runtime_config_sha256, "runtime_config_invalid"),
    ):
        _sha(value, code)
    if not isinstance(runtime_factory_entrypoint, str) or not runtime_factory_entrypoint:
        raise _fail("runtime_factory_entrypoint_invalid")
    if fx0_parity_artifact_sha256 is not None:
        _sha(fx0_parity_artifact_sha256, "fx0_parity_artifact_invalid")
    if method == "M*" and fx0_parity_artifact_sha256 is None:
        raise _fail("mstar_fx0_parity_artifact_required")
    if method != "M*" and fx0_parity_artifact_sha256 is not None:
        raise _fail("non_mstar_fx0_parity_artifact_forbidden")
    method_policy = {
        A0: {
            "configured_concurrency": 1,
            "scheduler": "FIFO_SINGLE_WORKER",
            "fx0_exact_parity_required": False,
        },
        P_STAR: {
            "configured_concurrency": 2,
            "scheduler": "WHOLE_UPDATE_TWO_WORKERS",
            "fx0_exact_parity_required": False,
        },
        "M*": {
            "configured_concurrency": 2,
            "scheduler": "PARALLEL_PREPARE_SOURCE_ORDERED_BIND",
            "fx0_exact_parity_required": True,
        },
    }[method]
    payload: dict[str, object] = {
        "schema_version": SCHEMA,
        "status": "FROZEN",
        "method": method,
        "graphiti_version": graphiti_version,
        "graphiti_commit": graphiti_commit,
        "graphiti_native_module": "graphiti_native",
        "graphiti_native_add_episode_qualname": "add_episode",
        "graphiti_episode_kwargs_qualname": "graphiti_episode_kwargs",
        "graphiti_native_source_sha256": graphiti_native_source_sha256,
        "graphiti_semantic_api_sha256": graphiti_semantic_api_sha256,
        "runtime_factory_entrypoint": runtime_factory_entrypoint,
        "runtime_factory_source_sha256": runtime_factory_source_sha256,
        "scheduler_source_sha256": scheduler_source_sha256,
        "scheduler_test_source_sha256": scheduler_test_source_sha256,
        "durable_store_source_sha256": durable_store_source_sha256,
        "durable_store_test_source_sha256": durable_store_test_source_sha256,
        "runtime_config_sha256": runtime_config_sha256,
        "fx0_parity_artifact_sha256": fx0_parity_artifact_sha256,
        "method_policy": method_policy,
        "qualification_status": "IDENTITY_ONLY_UNQUALIFIED",
        "failure_policy": {
            "failed_attempt_status": "incomplete_non_mergeable",
            "resume_authorized": False,
            "fresh_attempt_required": True,
            "db_commit_idempotence_claimed": False,
        },
    }
    _assert_public(payload)
    payload["identity_sha256"] = payload_sha256(payload)
    return verify_s5_production_identity(payload)


def verify_s5_production_identity(value: Mapping[str, object]) -> dict[str, object]:
    """Recompute and validate every public production identity field."""

    if not isinstance(value, Mapping):
        raise _fail("identity_not_mapping")
    identity = deepcopy(dict(value))
    _assert_public(identity)
    if set(identity) != _IDENTITY_FIELDS:
        raise _fail("identity_shape_invalid")
    if (
        identity.get("schema_version") != SCHEMA
        or identity.get("status") != "FROZEN"
        or identity.get("method") not in _METHODS
        or identity.get("graphiti_version") != GRAPHITI_VERSION
        or identity.get("graphiti_commit") != GRAPHITI_COMMIT
        or identity.get("graphiti_native_module") != "graphiti_native"
        or identity.get("graphiti_native_add_episode_qualname") != "add_episode"
        or identity.get("graphiti_episode_kwargs_qualname")
        != "graphiti_episode_kwargs"
        or not isinstance(identity.get("runtime_factory_entrypoint"), str)
        or not identity.get("runtime_factory_entrypoint")
        or identity.get("qualification_status") != "IDENTITY_ONLY_UNQUALIFIED"
    ):
        raise _fail("identity_binding_mismatch")
    for field in (
        "graphiti_native_source_sha256",
        "graphiti_semantic_api_sha256",
        "runtime_factory_source_sha256",
        "scheduler_source_sha256",
        "scheduler_test_source_sha256",
        "durable_store_source_sha256",
        "durable_store_test_source_sha256",
        "runtime_config_sha256",
    ):
        _sha(identity.get(field), f"{field}_invalid")
    fx0_artifact = identity.get("fx0_parity_artifact_sha256")
    if identity["method"] == "M*":
        _sha(fx0_artifact, "fx0_parity_artifact_invalid")
    elif fx0_artifact is not None:
        raise _fail("non_mstar_fx0_parity_artifact_forbidden")
    if identity.get("failure_policy") != {
        "failed_attempt_status": "incomplete_non_mergeable",
        "resume_authorized": False,
        "fresh_attempt_required": True,
        "db_commit_idempotence_claimed": False,
    }:
        raise _fail("failure_policy_mismatch")
    expected_policy = {
        A0: {
            "configured_concurrency": 1,
            "scheduler": "FIFO_SINGLE_WORKER",
            "fx0_exact_parity_required": False,
        },
        P_STAR: {
            "configured_concurrency": 2,
            "scheduler": "WHOLE_UPDATE_TWO_WORKERS",
            "fx0_exact_parity_required": False,
        },
        "M*": {
            "configured_concurrency": 2,
            "scheduler": "PARALLEL_PREPARE_SOURCE_ORDERED_BIND",
            "fx0_exact_parity_required": True,
        },
    }[identity["method"]]
    if identity.get("method_policy") != expected_policy:
        raise _fail("method_policy_mismatch")
    expected = payload_sha256(
        {key: item for key, item in identity.items() if key != "identity_sha256"}
    )
    if identity.get("identity_sha256") != expected:
        raise _fail("identity_hash_invalid")
    return identity


class S5ProductionRunner:
    """Compose a pinned Native binding, scheduler, and durable S5 attempt.

    Construction is side-effect free except for refusing an existing attempt
    directory.  The attempt manifest is created only when :meth:`run` starts;
    an exception or a fail-closed adapter result is never converted into resume
    authority.
    """

    def __init__(
        self,
        *,
        attempt_root: Path,
        spec: S5MethodSpec,
        identity: Mapping[str, object],
        graphiti: object,
        binding: S5GraphitiNativeBinding,
        episodes: Sequence[S5EpisodeRef],
    ) -> None:
        if not isinstance(spec, S5MethodSpec):
            raise _fail("method_spec_invalid")
        checked_identity = verify_s5_production_identity(identity)
        if checked_identity["method"] != spec.method:
            raise _fail("identity_method_mismatch")
        if checked_identity["graphiti_native_source_sha256"] != spec.native_path_identity_sha256:
            raise _fail("native_path_identity_mismatch")
        if graphiti is None:
            raise _fail("graphiti_missing")
        if not isinstance(binding, S5GraphitiNativeBinding):
            raise _fail("native_binding_invalid")
        selected = tuple(episodes)
        if (
            not selected
            or any(not isinstance(item, S5EpisodeRef) for item in selected)
            or [item.source_sequence for item in selected] != list(range(len(selected)))
        ):
            raise _fail("episodes_invalid")
        root = Path(attempt_root)
        if root.exists():
            raise _fail("attempt_exists")
        if spec.method == P_STAR and len(selected) < 2:
            raise _fail("p_c2_requires_at_least_two_episodes")
        self.attempt_root = root
        self.spec = spec
        self.identity = checked_identity
        self.graphiti = graphiti
        self.binding = binding
        self.episodes = selected

    async def run(self) -> dict[str, object]:
        """Execute one fresh A0 or P(C=2) attempt and seal its evidence."""

        store = S5AttemptStore.create(
            self.attempt_root,
            run_id=self.spec.run_id,
            method=self.spec.method,
            production_core_identity_sha256=self.identity["identity_sha256"],
            source_sha256s=tuple(item.source_sha256 for item in self.episodes),
        )

        async def persist_event(event: Mapping[str, object]) -> None:
            store.append_event(event)

        native_add_episode = build_native_add_episode_callable(
            graphiti=self.graphiti,
            binding=self.binding,
        )
        if self.spec.method == A0:
            evidence = await run_a0(
                spec=self.spec,
                episodes=self.episodes,
                native_add_episode=native_add_episode,
                persist_event=persist_event,
            )
        elif self.spec.method == P_STAR:
            evidence = await run_p_c2(
                spec=self.spec,
                episodes=self.episodes,
                native_add_episode=native_add_episode,
                persist_event=persist_event,
            )
        else:
            raise _fail("mstar_requires_fx0_production_adapter")

        evidence = dict(evidence)
        evidence["production_core_identity_sha256"] = self.identity["identity_sha256"]
        # The scheduler verifier intentionally does not know production
        # identity.  Bind it at the durable result boundary instead.
        finalized = store.finalize(evidence)
        return {
            **finalized,
            "payload": evidence,
            "production_identity_sha256": self.identity["identity_sha256"],
        }


__all__ = [
    "GRAPHITI_COMMIT",
    "GRAPHITI_VERSION",
    "S5ProductionIdentityError",
    "S5ProductionRunner",
    "build_s5_production_identity",
    "verify_s5_production_identity",
]
