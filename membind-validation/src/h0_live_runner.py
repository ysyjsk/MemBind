"""Dedicated, state-gated live orchestration for Protocol v1.3 H0.

The module intentionally does not reuse the legacy experiment runner: H0 has
stricter no-warmup, no-retry, manifest-binding, and interruption semantics.
Only sanitized counts, identifiers, flags, and hashes cross the checkpoint
boundary.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import argparse
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping

from h0_credentials import parse_h0_project_env
from h0_live_preflight import H0ReadinessCheckpointSink, run_h0_readiness_preflight
from h0_phase_runner import (
    H0SemanticEvidenceCollector,
    run_h0_a,
)
from h0_runtime import (
    H0AttemptLedger,
    H0BudgetError,
    H0CandidateConfig,
    H0CheckpointStore,
    H0InfrastructureError,
    H0ManifestError,
    H0QualificationError,
    H0QwenVLLMClient,
    H0SemanticError,
    H0StateGateError,
    H0WireObserver,
    VLLMChatTokenCounter,
    authorize_h0_live_entry,
    build_h0_openai_client,
    canonical_json_sha256,
    load_h0_calibration_corpus,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = ROOT / "CURRENT_STATE.json"
DEFAULT_ARTIFACTS_ROOT = ROOT / "artifacts/h0_runs"


class H0ConstructionCredentialLoader:
    """Read construction credentials only from the ignored project ``.env``."""

    def __init__(
        self,
        *,
        root: str | Path,
        expected_base_url: str,
        expected_model: str,
        env_file_loader: Callable[[str | Path], Mapping[str, str]] = parse_h0_project_env,
    ) -> None:
        self.root = Path(root).resolve()
        self.expected_base_url = expected_base_url
        self.expected_model = expected_model
        self.env_file_loader = env_file_loader

    def __call__(self) -> dict[str, str]:
        loaded = self.env_file_loader(self.root / ".env")
        if not isinstance(loaded, Mapping):
            raise H0ManifestError("project credential file loader returned invalid data")
        api_key = loaded.get("CONSTRUCTION_LLM_API_KEY") or loaded.get("VLLM_API_KEY")
        base_url = loaded.get("CONSTRUCTION_LLM_BASE_URL")
        model = loaded.get("CONSTRUCTION_LLM_MODEL")
        if not isinstance(api_key, str) or not api_key:
            raise H0ManifestError("project credential file lacks construction API key")
        if base_url != self.expected_base_url:
            raise H0ManifestError("project construction endpoint differs from manifest")
        if model != self.expected_model:
            raise H0ManifestError("project construction model differs from manifest")
        return {"base_url": base_url, "api_key": api_key}


class _CachedCredentialLoader:
    """Ensure readiness and generation share one project-env read."""

    def __init__(self, source: Callable[[], Mapping[str, Any]]) -> None:
        self.source = source
        self.value: dict[str, Any] | None = None

    def __call__(self) -> dict[str, Any]:
        if self.value is None:
            loaded = self.source()
            if not isinstance(loaded, Mapping):
                raise H0ManifestError("H0 credential loader returned invalid data")
            self.value = dict(loaded)
        return dict(self.value)


class H0AClientFactory:
    """Create one fresh, H0-pinned client and tokenizer per repeated trial."""

    def __init__(
        self,
        *,
        definition: Any,
        credentials: Mapping[str, Any],
        ledger: H0AttemptLedger,
        semantic_collector: H0SemanticEvidenceCollector,
        completion_transport_factory: Callable[[], Any] | None = None,
        tokenize_transport_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.definition = definition
        self.credentials = dict(credentials)
        self.ledger = ledger
        self.semantic_collector = semantic_collector
        self.completion_transport_factory = completion_transport_factory
        self.tokenize_transport_factory = tokenize_transport_factory
        self.tokenize_event_groups: list[list[dict[str, Any]]] = []
        self.wire_event_groups: list[list[dict[str, Any]]] = []
        self.created_client_count = 0

    def __call__(
        self,
        repeated_trial_index: int,
        ledger: H0AttemptLedger,
        semantic_collector: H0SemanticEvidenceCollector,
    ) -> Any:
        if ledger is not self.ledger or semantic_collector is not self.semantic_collector:
            raise H0ManifestError("H0-A factory received a different stage ledger")
        identity = self.definition.identity
        base_url = self.credentials.get("base_url")
        api_key = self.credentials.get("api_key")
        if base_url != identity["base_url"] or not isinstance(api_key, str) or not api_key:
            raise H0ManifestError("H0-A credentials differ from the resolved identity")

        from graphiti_core.llm_client.config import LLMConfig

        observer = H0WireObserver()
        openai_client = build_h0_openai_client(
            api_key=api_key,
            base_url=base_url,
            observer=observer,
            transport=(
                self.completion_transport_factory()
                if self.completion_transport_factory is not None
                else None
            ),
        )
        counter = VLLMChatTokenCounter(
            base_url=base_url,
            model=identity["served_model_id"],
            api_key=api_key,
            transport=(
                self.tokenize_transport_factory()
                if self.tokenize_transport_factory is not None
                else None
            ),
        )
        self.wire_event_groups.append(observer.events)
        self.tokenize_event_groups.append(counter.events)
        self.created_client_count += 1
        candidate: H0CandidateConfig = self.definition.candidate
        try:
            return H0QwenVLLMClient(
                config=LLMConfig(
                    api_key="credential-held-by-injected-client",
                    model=identity["served_model_id"],
                    small_model=identity["served_model_id"],
                    base_url=base_url,
                    temperature=candidate.temperature,
                    max_tokens=candidate.requested_max_tokens,
                ),
                candidate=candidate,
                token_counter=counter,
                semantic_guardrail=self.definition.semantic_guardrail,
                semantic_evidence_sink=semantic_collector,
                ledger=ledger,
                repeated_trial_index=repeated_trial_index,
                client=openai_client,
            )
        except Exception:
            # Constructors perform no network I/O, but both allocated transports
            # still need deterministic cleanup if local validation fails.
            async def close_allocated() -> None:
                await counter.close()
                await openai_client.close()

            try:
                asyncio.get_running_loop().create_task(close_allocated())
            finally:
                raise

    def safe_runtime_evidence(self) -> dict[str, Any]:
        return {
            "fresh_client_count": self.created_client_count,
            "tokenize_events": deepcopy(self.tokenize_event_groups),
            "wire_events": deepcopy(self.wire_event_groups),
            "db_calls": 0,
            "embedding_calls": 0,
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }


def build_h0_a_client_factory(
    *,
    definition: Any,
    credentials: Mapping[str, Any],
    ledger: H0AttemptLedger,
    semantic_collector: H0SemanticEvidenceCollector,
    completion_transport_factory: Callable[[], Any] | None = None,
    tokenize_transport_factory: Callable[[], Any] | None = None,
) -> H0AClientFactory:
    return H0AClientFactory(
        definition=definition,
        credentials=credentials,
        ledger=ledger,
        semantic_collector=semantic_collector,
        completion_transport_factory=completion_transport_factory,
        tokenize_transport_factory=tokenize_transport_factory,
    )


def _load_primary_h0_record(root: Path) -> dict[str, Any]:
    split_path = root / "artifacts/dataset/frozen_split_v1_3.json"
    try:
        split = json.loads(split_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise H0ManifestError("H0 calibration split is missing or invalid") from None
    if not isinstance(split, dict) or not isinstance(split.get("source_path"), str):
        raise H0ManifestError("H0 calibration split has no source path")
    corpus = load_h0_calibration_corpus(split_path, split["source_path"])
    return corpus.require("07741c45")


def _default_runtime_definition_loader(
    authorization: Mapping[str, Any], *, root: Path
) -> Any:
    # Imported lazily so the gate-order tests can prove that denied execution
    # never opens any manifest or artifact.
    from h0_live_preflight import load_authorized_h0_runtime_definition

    return load_authorized_h0_runtime_definition(authorization, root=root)


def _validate_definition(definition: Any) -> None:
    identity = getattr(definition, "identity", None)
    candidate = getattr(definition, "candidate", None)
    guardrail = getattr(definition, "semantic_guardrail", None)
    digest = getattr(definition, "definition_sha256", None)
    if (
        not isinstance(identity, Mapping)
        or identity.get("candidate_id") != "Q1"
        or identity.get("phase") != "H0-A"
        or getattr(candidate, "candidate_id", None) != "Q1"
        or not isinstance(guardrail, Mapping)
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        raise H0ManifestError("authorized H0 runtime definition is invalid")


def _same_authorization_loader(
    expected: Mapping[str, Any], identity: Mapping[str, Any]
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    frozen = dict(expected)

    def load(observed: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(observed, Mapping) or dict(observed) != frozen:
            raise H0StateGateError("H0 live authorization changed during readiness")
        return dict(identity)

    return load


def _failure_code(error: BaseException) -> str:
    if isinstance(error, H0SemanticError):
        return "semantic_utility_failure"
    if isinstance(error, H0BudgetError):
        return "context_budget_failure"
    if isinstance(error, H0QualificationError):
        return "candidate_qualification_failure"
    if isinstance(error, H0StateGateError):
        return "authorization_revoked"
    if isinstance(error, H0ManifestError):
        return "manifest_contract_failure"
    return "candidate_execution_failure"


def _print_progress(event: Mapping[str, Any]) -> None:
    print(json.dumps(dict(event), ensure_ascii=True, sort_keys=True), flush=True)


async def execute_h0_a_live(
    *,
    root: str | Path = ROOT,
    state_path: str | Path = DEFAULT_STATE_PATH,
    artifacts_root: str | Path = DEFAULT_ARTIFACTS_ROOT,
    stage_attempt_id: str,
    authorization_checker: Callable[..., Any] = authorize_h0_live_entry,
    runtime_definition_loader: Callable[..., Any] = _default_runtime_definition_loader,
    record_loader: Callable[[Path], Mapping[str, Any]] = _load_primary_h0_record,
    credential_loader: Callable[[], Mapping[str, Any]] | None = None,
    checkpoint_store_factory: Callable[..., H0CheckpointStore] = H0CheckpointStore,
    readiness_runner: Callable[..., Any] = run_h0_readiness_preflight,
    client_factory_builder: Callable[..., Any] = build_h0_a_client_factory,
    phase_runner: Callable[..., Any] = run_h0_a,
    progress_sink: Callable[[dict[str, Any]], Any] = _print_progress,
) -> dict[str, Any]:
    """Run one authorized Q1/H0-A attempt with durable stop semantics."""

    root_path = Path(root).resolve()
    state_path = Path(state_path)
    artifacts_path = Path(artifacts_root)

    # This is deliberately the first externally supplied call. No directory,
    # manifest, dataset, environment file, or client is touched before it.
    authorization = authorization_checker(
        state_path=state_path,
        candidate_id="Q1",
        phase="H0-A",
    )
    if not isinstance(authorization, Mapping):
        raise H0ManifestError("H0 authorization did not return manifest bindings")
    authorized_attempt_id = authorization.get("authorized_stage_attempt_id")
    repair_admission = authorization.get("repair_admission")
    if authorized_attempt_id is not None and not (
        isinstance(authorized_attempt_id, str)
        and authorized_attempt_id == stage_attempt_id
        and isinstance(repair_admission, Mapping)
        and repair_admission.get("replacement_attempt_id") == stage_attempt_id
    ):
        raise H0StateGateError("H0-A replacement attempt is not authorized")
    definition = runtime_definition_loader(authorization, root=root_path)
    _validate_definition(definition)

    store = checkpoint_store_factory(
        root=artifacts_path,
        stage_attempt_id=stage_attempt_id,
        candidate_id="Q1",
        phase="H0-A",
        progress_sink=progress_sink,
        repair_admission=authorization.get("repair_admission"),
    )
    readiness_sink = H0ReadinessCheckpointSink(store)
    if credential_loader is None:
        credential_loader = H0ConstructionCredentialLoader(
            root=root_path,
            expected_base_url=definition.identity["base_url"],
            expected_model=definition.identity["served_model_id"],
        )
    cached_credentials = _CachedCredentialLoader(credential_loader)
    ledger: H0AttemptLedger | None = None
    client_factory: Any | None = None

    try:
        readiness = await readiness_runner(
            state_path=state_path,
            stage_attempt_id=stage_attempt_id,
            candidate_id="Q1",
            phase="H0-A",
            credential_loader=cached_credentials,
            resolved_identity_loader=_same_authorization_loader(
                authorization, definition.identity
            ),
            authorization_checker=authorization_checker,
            progress_sink=readiness_sink,
        )
        if (
            not isinstance(readiness, Mapping)
            or readiness.get("status") != "ready"
            or readiness.get("authorized_candidate_execution_ready") is not True
            or readiness.get("generation_requests") != 0
        ):
            raise H0ManifestError("H0 readiness result is not generation-safe")
        refreshed_authorization = authorization_checker(
            state_path=state_path,
            candidate_id="Q1",
            phase="H0-A",
        )
        if (
            not isinstance(refreshed_authorization, Mapping)
            or dict(refreshed_authorization) != dict(authorization)
        ):
            raise H0StateGateError("H0 live authorization changed after readiness")
        store.record_segment("readiness_result", "ready", readiness)

        record = record_loader(root_path)
        if not isinstance(record, Mapping):
            raise H0ManifestError("H0-A record loader returned invalid data")
        ledger = H0AttemptLedger(stage_attempt_id=stage_attempt_id)
        semantic_collector = H0SemanticEvidenceCollector()
        client_factory = client_factory_builder(
            definition=definition,
            credentials=cached_credentials(),
            ledger=ledger,
            semantic_collector=semantic_collector,
        )

        async def checkpoint_trial(event: Mapping[str, Any]) -> None:
            repeated = event.get("repeated_trial_index")
            if isinstance(repeated, bool) or not isinstance(repeated, int):
                raise H0ManifestError("H0-A checkpoint has invalid trial index")
            store.record_segment(
                "logical_trial",
                f"trial-{repeated:03d}",
                {
                    "phase_checkpoint": dict(event),
                    "attempt_ledger": ledger.safe_artifact(),
                    "runtime_evidence": client_factory.safe_runtime_evidence(),
                    "runtime_definition_sha256": definition.definition_sha256,
                },
            )

        phase_result = await phase_runner(
            record=record,
            stage_attempt_id=stage_attempt_id,
            client_factory=client_factory,
            ledger=ledger,
            semantic_collector=semantic_collector,
            semantic_guardrail=definition.semantic_guardrail,
            trial_checkpoint=checkpoint_trial,
        )
        if not isinstance(phase_result, Mapping) or phase_result.get("qualified") is not True:
            raise H0QualificationError("H0-A phase did not qualify")
        phase_result = dict(phase_result)
        terminal_hash = canonical_json_sha256(phase_result)
        store.record_segment(
            "stage_result",
            "qualified",
            {
                "phase_result": phase_result,
                "phase_result_sha256": terminal_hash,
                "attempt_ledger": ledger.safe_artifact(),
                "runtime_evidence": client_factory.safe_runtime_evidence(),
                "runtime_definition_sha256": definition.definition_sha256,
            },
        )
        terminal = store.mark_stage_complete(terminal_hash)
        return {
            "schema_version": "membind.h0.live-run-result.v1",
            "protocol_version": "current-validation-v1.3",
            "candidate_id": "Q1",
            "phase": "H0-A",
            "stage_attempt_id": stage_attempt_id,
            "status": terminal["status"],
            "phase_result": phase_result,
            "checkpoint_index_path": terminal["checkpoint_index_path"],
            "checkpoint_index_sha256": terminal["checkpoint_index_sha256"],
            "runtime_definition_sha256": definition.definition_sha256,
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }
    except H0InfrastructureError:
        if store.index.get("status") == "running":
            ledger_artifact = (
                ledger.safe_artifact()
                if ledger is not None
                else {
                    "schema_version": "membind.h0.attempt-ledger.v1",
                    "stage_attempt_id": stage_attempt_id,
                    "logical_trials": [],
                    "http_attempts": [],
                    "secrets_persisted": False,
                    "raw_prompts_persisted": False,
                    "raw_responses_persisted": False,
                }
            )
            runtime_evidence = (
                client_factory.safe_runtime_evidence()
                if client_factory is not None
                else {"tokenize_events": [], "wire_events": []}
            )
            store.record_segment(
                "infrastructure_failure",
                "vllm-unreachable",
                {
                    "failure_code": "vllm_unreachable",
                    "attempt_ledger": ledger_artifact,
                    "runtime_evidence": runtime_evidence,
                    "runtime_definition_sha256": definition.definition_sha256,
                    "candidate_advance_allowed": False,
                },
            )
            store.mark_infrastructure_interruption("vllm_unreachable")
        raise
    except Exception as exc:
        if store.index.get("status") == "running":
            code = _failure_code(exc)
            failure_payload = {
                "failure_code": code,
                "attempt_ledger": (
                    ledger.safe_artifact()
                    if ledger is not None
                    else {
                        "schema_version": "membind.h0.attempt-ledger.v1",
                        "stage_attempt_id": stage_attempt_id,
                        "logical_trials": [],
                        "http_attempts": [],
                        "secrets_persisted": False,
                        "raw_prompts_persisted": False,
                        "raw_responses_persisted": False,
                    }
                ),
                "runtime_evidence": (
                    client_factory.safe_runtime_evidence()
                    if client_factory is not None
                    else {"tokenize_events": [], "wire_events": []}
                ),
                "runtime_definition_sha256": definition.definition_sha256,
                "candidate_advance_allowed": False,
            }
            evidence_sha = canonical_json_sha256(failure_payload)
            store.record_segment("candidate_failure", code, failure_payload)
            store.mark_candidate_failure(code, evidence_sha)
        raise


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run one state-authorized H0 stage")
    subparsers = parser.add_subparsers(dest="command", required=True)
    h0_a = subparsers.add_parser("run-q1-h0-a")
    h0_a.add_argument("--attempt-id", required=True)
    h0_a.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    h0_a.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS_ROOT)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            execute_h0_a_live(
                state_path=args.state,
                artifacts_root=args.artifacts,
                stage_attempt_id=args.attempt_id,
            )
        )
    except H0InfrastructureError:
        print("H0 stopped: vllm_unreachable; inspect the durable checkpoint", flush=True)
        return 20
    except (H0StateGateError, H0ManifestError, H0QualificationError, H0SemanticError):
        print("H0 stopped: state, manifest, or candidate qualification failure", flush=True)
        return 10
    print(json.dumps(result, ensure_ascii=True, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
