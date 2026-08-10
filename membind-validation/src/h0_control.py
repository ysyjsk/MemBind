"""Explicit Protocol v1.3 H0 control-plane commands.

The controller keeps offline resolution, state binding, live authorization,
live revocation, and live execution as separate operator actions.  In
particular, ``run-q1-a`` never changes authorization state or advances to
another candidate.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from h0_artifacts import (
    H0ArtifactVerificationError,
    verify_h0_offline_artifacts,
    write_h0_offline_artifacts,
)
from h0_live_runner import execute_h0_a_live
from h0_full_history_live import execute_h0_full_history_live
from h0_harness_recovery import (
    H0HarnessRecoveryError,
    transition_h0_b_harness_repair_bound,
    transition_h0_b_harness_revoke,
    transition_h0_b_infrastructure_interrupted,
    transition_h0_b_infrastructure_rerun_bound,
    transition_h0_b_infrastructure_rerun_live,
    transition_h0_b_post_workload_harness_repair_bound,
    transition_h0_b_post_workload_harness_replacement_live,
    transition_h0_b_post_workload_harness_revoke,
    transition_h0_b_replacement_live,
)
from h0_repair_admission import (
    H0RepairAdmissionError,
    write_h0_b_harness_repair_decision,
    write_h0_b_infrastructure_rerun_decision,
    write_h0_b_post_workload_harness_repair_decision,
    write_h0_repair_decision,
)
from h0_phase_state import (
    H0PhaseStateError,
    transition_h0_repair_bound,
    transition_h0_replacement_live,
    transition_h0_successor_phase_live,
)
from h0_runtime import (
    H0BudgetError,
    H0DataScopeError,
    H0InfrastructureError,
    H0ManifestError,
    H0QualificationError,
    H0SemanticError,
    H0StateGateError,
)
from h0_state_transition import (
    H0StateTransitionError,
    transition_h0_live_authorization_revoke,
    transition_q1_h0_a_live,
    verify_and_persist_h0_offline_bound_state,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = Path("CURRENT_STATE.json")
DEFAULT_RUN_ARTIFACTS = Path("artifacts/h0_runs")


class H0ArgumentError(RuntimeError):
    """Raised without retaining argparse's potentially sensitive message."""


class H0ControlInputError(RuntimeError):
    """Raised for malformed explicit controller input."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise H0ArgumentError("argument_error")


def _resolve_under_root(root: Path, value: str | Path) -> Path:
    path = Path(value)
    candidate = (path if path.is_absolute() else root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise H0ControlInputError("path_outside_experiment_root") from None
    return candidate


def _read_tdd_evidence(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise H0ControlInputError("invalid_tdd_evidence") from None
    if not isinstance(value, dict):
        raise H0ControlInputError("invalid_tdd_evidence")
    return value


def _safe_success(command: str, result: Mapping[str, Any], **extra: Any) -> None:
    fields = {
        "command": command,
        "outcome": "success",
        "status": result.get("status", "completed"),
        **extra,
    }
    for name in (
        "index_path",
        "index_sha256",
        "checkpoint_index_path",
        "checkpoint_index_sha256",
        "current_action_scope",
        "live_h0_candidate_authorized",
        "authorized_live_actions",
        "live_eligible",
    ):
        if name in result:
            fields[name] = result[name]
    print(json.dumps(fields, ensure_ascii=True, sort_keys=True), flush=True)


def _safe_failure(reason_code: str) -> None:
    print(
        json.dumps(
            {"outcome": "failure", "reason_code": reason_code},
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description="Control Protocol v1.3 H0 explicitly")
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("offline-resolve")

    prepare_repair = commands.add_parser("prepare-repair")
    prepare_repair.add_argument("--replacement-attempt-id", required=True)

    prepare_h0_b_repair = commands.add_parser("prepare-h0-b-harness-repair")
    prepare_h0_b_repair.add_argument("--replacement-attempt-id", required=True)

    prepare_h0_b_infra = commands.add_parser(
        "prepare-h0-b-infrastructure-rerun"
    )
    prepare_h0_b_infra.add_argument("--replacement-attempt-id", required=True)

    prepare_h0_b_post_workload = commands.add_parser(
        "prepare-h0-b-post-workload-harness-repair"
    )
    prepare_h0_b_post_workload.add_argument(
        "--replacement-attempt-id", required=True
    )

    bind_repair = commands.add_parser("bind-repair")
    bind_repair.add_argument("--state", type=Path, default=DEFAULT_STATE)
    bind_repair.add_argument("--tdd-evidence", type=Path, required=True)
    bind_repair.add_argument("--decision", type=Path, required=True)
    bind_repair.add_argument("--decision-sha256", required=True)
    bind_repair.add_argument("--commit", action="store_true")

    authorize_replacement = commands.add_parser("authorize-q1-a-replacement")
    authorize_replacement.add_argument("--state", type=Path, default=DEFAULT_STATE)
    authorize_replacement.add_argument("--commit", action="store_true")

    bind_h0_b_repair = commands.add_parser("bind-h0-b-harness-repair")
    bind_h0_b_repair.add_argument("--state", type=Path, default=DEFAULT_STATE)
    bind_h0_b_repair.add_argument("--tdd-evidence", type=Path, required=True)
    bind_h0_b_repair.add_argument("--decision", type=Path, required=True)
    bind_h0_b_repair.add_argument("--decision-sha256", required=True)
    bind_h0_b_repair.add_argument("--commit", action="store_true")

    authorize_h0_b_replacement = commands.add_parser(
        "authorize-q1-b-replacement"
    )
    authorize_h0_b_replacement.add_argument(
        "--state", type=Path, default=DEFAULT_STATE
    )
    authorize_h0_b_replacement.add_argument("--commit", action="store_true")

    bind_h0_b_infra = commands.add_parser("bind-h0-b-infrastructure-rerun")
    bind_h0_b_infra.add_argument("--state", type=Path, default=DEFAULT_STATE)
    bind_h0_b_infra.add_argument("--tdd-evidence", type=Path, required=True)
    bind_h0_b_infra.add_argument("--decision", type=Path, required=True)
    bind_h0_b_infra.add_argument("--decision-sha256", required=True)
    bind_h0_b_infra.add_argument("--commit", action="store_true")

    authorize_h0_b_infra = commands.add_parser(
        "authorize-q1-b-infrastructure-rerun"
    )
    authorize_h0_b_infra.add_argument("--state", type=Path, default=DEFAULT_STATE)
    authorize_h0_b_infra.add_argument("--commit", action="store_true")

    bind_h0_b_post_workload = commands.add_parser(
        "bind-h0-b-post-workload-harness-repair"
    )
    bind_h0_b_post_workload.add_argument(
        "--state", type=Path, default=DEFAULT_STATE
    )
    bind_h0_b_post_workload.add_argument(
        "--tdd-evidence", type=Path, required=True
    )
    bind_h0_b_post_workload.add_argument("--decision", type=Path, required=True)
    bind_h0_b_post_workload.add_argument("--decision-sha256", required=True)
    bind_h0_b_post_workload.add_argument("--commit", action="store_true")

    authorize_h0_b_post_workload = commands.add_parser(
        "authorize-q1-b-post-workload-harness-replacement"
    )
    authorize_h0_b_post_workload.add_argument(
        "--state", type=Path, default=DEFAULT_STATE
    )
    authorize_h0_b_post_workload.add_argument("--commit", action="store_true")

    advance = commands.add_parser("advance-q1")
    advance.add_argument("--state", type=Path, default=DEFAULT_STATE)
    advance.add_argument("--completed-phase", choices=("H0-A", "H0-B"), required=True)
    advance.add_argument("--attempt-id", required=True)
    advance.add_argument("--checkpoint-index", type=Path, required=True)
    advance.add_argument("--checkpoint-index-sha256", required=True)
    advance.add_argument("--runtime-definition-sha256", required=True)
    advance.add_argument("--commit", action="store_true")

    bind = commands.add_parser("bind")
    bind.add_argument("--state", type=Path, default=DEFAULT_STATE)
    bind.add_argument("--tdd-evidence", type=Path, required=True)

    authorize = commands.add_parser("authorize-q1-a")
    authorize.add_argument("--state", type=Path, default=DEFAULT_STATE)
    authorize.add_argument("--commit", action="store_true")

    revoke = commands.add_parser("revoke-h0-live")
    revoke.add_argument("--state", type=Path, default=DEFAULT_STATE)
    revoke.add_argument("--candidate-id", required=True)
    revoke.add_argument("--phase", required=True)
    revoke.add_argument("--attempt-id", required=True)
    revoke.add_argument("--checkpoint-index", type=Path, required=True)
    revoke.add_argument("--checkpoint-index-sha256", required=True)
    revoke.add_argument("--commit", action="store_true")

    revoke_h0_b = commands.add_parser("revoke-h0-b-harness")
    revoke_h0_b.add_argument("--state", type=Path, default=DEFAULT_STATE)
    revoke_h0_b.add_argument("--attempt-id", required=True)
    revoke_h0_b.add_argument("--checkpoint-index", type=Path, required=True)
    revoke_h0_b.add_argument("--checkpoint-index-sha256", required=True)
    revoke_h0_b.add_argument("--failure-report", type=Path, required=True)
    revoke_h0_b.add_argument("--failure-report-sha256", required=True)
    revoke_h0_b.add_argument("--commit", action="store_true")

    revoke_h0_b_infra = commands.add_parser("revoke-h0-b-infrastructure")
    revoke_h0_b_infra.add_argument("--state", type=Path, default=DEFAULT_STATE)
    revoke_h0_b_infra.add_argument("--attempt-id", required=True)
    revoke_h0_b_infra.add_argument("--checkpoint-index", type=Path, required=True)
    revoke_h0_b_infra.add_argument("--checkpoint-index-sha256", required=True)
    revoke_h0_b_infra.add_argument("--commit", action="store_true")

    revoke_h0_b_post_workload = commands.add_parser(
        "revoke-h0-b-post-workload-harness"
    )
    revoke_h0_b_post_workload.add_argument(
        "--state", type=Path, default=DEFAULT_STATE
    )
    revoke_h0_b_post_workload.add_argument("--attempt-id", required=True)
    for option in (
        "checkpoint-index",
        "failure-segment",
        "source-checkpoint",
        "live-log",
        "offline-probe",
    ):
        revoke_h0_b_post_workload.add_argument(f"--{option}", type=Path, required=True)
        revoke_h0_b_post_workload.add_argument(
            f"--{option}-sha256", required=True
        )
    revoke_h0_b_post_workload.add_argument("--commit", action="store_true")

    run = commands.add_parser("run-q1-a")
    run.add_argument("--state", type=Path, default=DEFAULT_STATE)
    run.add_argument("--artifacts", type=Path, default=DEFAULT_RUN_ARTIFACTS)
    run.add_argument("--attempt-id", required=True)
    for command, phase in (("run-q1-b", "H0-B"), ("run-q1-c", "H0-C")):
        full = commands.add_parser(command)
        full.add_argument("--state", type=Path, default=DEFAULT_STATE)
        full.add_argument("--artifacts", type=Path, default=DEFAULT_RUN_ARTIFACTS)
        full.add_argument("--attempt-id", required=True)
        full.set_defaults(h0_phase=phase)
    return parser


def _run_command(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    if args.command == "offline-resolve":
        write_h0_offline_artifacts(root)
        verified = verify_h0_offline_artifacts(root)
        _safe_success(args.command, verified)
        return

    if args.command == "prepare-repair":
        verification = verify_h0_offline_artifacts(root)
        result = write_h0_repair_decision(
            root=root,
            manifest_verification=verification,
            replacement_attempt_id=args.replacement_attempt_id,
        )
        _safe_success(args.command, result, **result)
        return

    if args.command == "prepare-h0-b-harness-repair":
        verification = verify_h0_offline_artifacts(root)
        result = write_h0_b_harness_repair_decision(
            root=root,
            manifest_verification=verification,
            replacement_attempt_id=args.replacement_attempt_id,
        )
        _safe_success(args.command, result, **result)
        return

    if args.command == "prepare-h0-b-infrastructure-rerun":
        verification = verify_h0_offline_artifacts(root)
        result = write_h0_b_infrastructure_rerun_decision(
            root=root,
            manifest_verification=verification,
            replacement_attempt_id=args.replacement_attempt_id,
        )
        _safe_success(args.command, result, **result)
        return

    if args.command == "prepare-h0-b-post-workload-harness-repair":
        verification = verify_h0_offline_artifacts(root)
        result = write_h0_b_post_workload_harness_repair_decision(
            root=root,
            manifest_verification=verification,
            replacement_attempt_id=args.replacement_attempt_id,
        )
        _safe_success(args.command, result, **result)
        return

    state_path = _resolve_under_root(root, args.state)
    if args.command == "revoke-h0-b-post-workload-harness":
        paths = {
            name: _resolve_under_root(root, getattr(args, name))
            for name in (
                "checkpoint_index",
                "failure_segment",
                "source_checkpoint",
                "live_log",
                "offline_probe",
            )
        }
        result = transition_h0_b_post_workload_harness_revoke(
            state_path,
            root=root,
            stage_attempt_id=args.attempt_id,
            checkpoint_index_path=paths["checkpoint_index"].relative_to(root).as_posix(),
            checkpoint_index_sha256=args.checkpoint_index_sha256,
            failure_segment_path=paths["failure_segment"].relative_to(root).as_posix(),
            failure_segment_sha256=args.failure_segment_sha256,
            source_checkpoint_path=paths["source_checkpoint"].relative_to(root).as_posix(),
            source_checkpoint_sha256=args.source_checkpoint_sha256,
            live_log_path=paths["live_log"].relative_to(root).as_posix(),
            live_log_sha256=args.live_log_sha256,
            offline_probe_path=paths["offline_probe"].relative_to(root).as_posix(),
            offline_probe_sha256=args.offline_probe_sha256,
            dry_run=not args.commit,
        )
        _safe_success(
            args.command,
            result,
            committed=bool(args.commit),
            reason="post_workload_execution_harness_interface_contract",
            candidate_rerun_authorized=False,
            candidate_advance_authorized=False,
        )
        return
    if args.command == "revoke-h0-b-infrastructure":
        checkpoint_path = _resolve_under_root(root, args.checkpoint_index)
        result = transition_h0_b_infrastructure_interrupted(
            state_path,
            root=root,
            stage_attempt_id=args.attempt_id,
            checkpoint_index_path=checkpoint_path.relative_to(root).as_posix(),
            checkpoint_index_sha256=args.checkpoint_index_sha256,
            dry_run=not args.commit,
        )
        _safe_success(
            args.command,
            result,
            committed=bool(args.commit),
            reason="construction_vllm_unreachable_before_model_workload",
            candidate_rerun_authorized=False,
            candidate_advance_authorized=False,
        )
        return
    if args.command == "revoke-h0-b-harness":
        checkpoint_path = _resolve_under_root(root, args.checkpoint_index)
        report_path = _resolve_under_root(root, args.failure_report)
        result = transition_h0_b_harness_revoke(
            state_path,
            root=root,
            stage_attempt_id=args.attempt_id,
            checkpoint_index_path=checkpoint_path.relative_to(root).as_posix(),
            checkpoint_index_sha256=args.checkpoint_index_sha256,
            failure_report_path=report_path.relative_to(root).as_posix(),
            failure_report_sha256=args.failure_report_sha256,
            dry_run=not args.commit,
        )
        _safe_success(
            args.command,
            result,
            committed=bool(args.commit),
            reason="h0_b_pre_workload_harness_compatibility_failure",
            candidate_rerun_authorized=False,
            candidate_advance_authorized=False,
        )
        return

    if args.command == "bind-h0-b-harness-repair":
        evidence_path = _resolve_under_root(root, args.tdd_evidence)
        evidence = _read_tdd_evidence(evidence_path)
        decision_path = _resolve_under_root(root, args.decision)
        verification = verify_h0_offline_artifacts(root)
        result = transition_h0_b_harness_repair_bound(
            state_path,
            root=root,
            manifest_verification=verification,
            tdd_evidence=evidence,
            repair_decision_path=decision_path.relative_to(root).as_posix(),
            repair_decision_sha256=args.decision_sha256,
            dry_run=not args.commit,
        )
        _safe_success(args.command, result, committed=bool(args.commit))
        return

    if args.command == "authorize-q1-b-replacement":
        result = transition_h0_b_replacement_live(
            state_path,
            root=root,
            dry_run=not args.commit,
        )
        _safe_success(args.command, result, committed=bool(args.commit))
        return

    if args.command == "bind-h0-b-infrastructure-rerun":
        evidence_path = _resolve_under_root(root, args.tdd_evidence)
        evidence = _read_tdd_evidence(evidence_path)
        decision_path = _resolve_under_root(root, args.decision)
        verification = verify_h0_offline_artifacts(root)
        result = transition_h0_b_infrastructure_rerun_bound(
            state_path,
            root=root,
            manifest_verification=verification,
            tdd_evidence=evidence,
            rerun_decision_path=decision_path.relative_to(root).as_posix(),
            rerun_decision_sha256=args.decision_sha256,
            dry_run=not args.commit,
        )
        _safe_success(args.command, result, committed=bool(args.commit))
        return

    if args.command == "authorize-q1-b-infrastructure-rerun":
        result = transition_h0_b_infrastructure_rerun_live(
            state_path,
            root=root,
            dry_run=not args.commit,
        )
        _safe_success(args.command, result, committed=bool(args.commit))
        return

    if args.command == "bind-h0-b-post-workload-harness-repair":
        evidence_path = _resolve_under_root(root, args.tdd_evidence)
        evidence = _read_tdd_evidence(evidence_path)
        decision_path = _resolve_under_root(root, args.decision)
        verification = verify_h0_offline_artifacts(root)
        result = transition_h0_b_post_workload_harness_repair_bound(
            state_path,
            root=root,
            manifest_verification=verification,
            tdd_evidence=evidence,
            repair_decision_path=decision_path.relative_to(root).as_posix(),
            repair_decision_sha256=args.decision_sha256,
            dry_run=not args.commit,
        )
        _safe_success(args.command, result, committed=bool(args.commit))
        return

    if args.command == "authorize-q1-b-post-workload-harness-replacement":
        result = transition_h0_b_post_workload_harness_replacement_live(
            state_path,
            root=root,
            dry_run=not args.commit,
        )
        _safe_success(args.command, result, committed=bool(args.commit))
        return

    if args.command == "bind-repair":
        evidence_path = _resolve_under_root(root, args.tdd_evidence)
        evidence = _read_tdd_evidence(evidence_path)
        decision_path = _resolve_under_root(root, args.decision)
        decision_relative = decision_path.relative_to(root).as_posix()
        verification = verify_h0_offline_artifacts(root)
        result = transition_h0_repair_bound(
            state_path,
            root=root,
            manifest_verification=verification,
            tdd_evidence=evidence,
            repair_decision_path=decision_relative,
            repair_decision_sha256=args.decision_sha256,
            dry_run=not args.commit,
        )
        _safe_success(args.command, result, committed=bool(args.commit))
        return

    if args.command == "authorize-q1-a-replacement":
        result = transition_h0_replacement_live(
            state_path,
            root=root,
            dry_run=not args.commit,
        )
        _safe_success(args.command, result, committed=bool(args.commit))
        return

    if args.command == "advance-q1":
        checkpoint_path = _resolve_under_root(root, args.checkpoint_index)
        result = transition_h0_successor_phase_live(
            state_path,
            root=root,
            completed_phase=args.completed_phase,
            stage_attempt_id=args.attempt_id,
            checkpoint_index_path=checkpoint_path.relative_to(root).as_posix(),
            checkpoint_index_sha256=args.checkpoint_index_sha256,
            runtime_definition_sha256=args.runtime_definition_sha256,
            dry_run=not args.commit,
        )
        _safe_success(args.command, result, committed=bool(args.commit))
        return
    if args.command == "bind":
        evidence_path = _resolve_under_root(root, args.tdd_evidence)
        evidence = _read_tdd_evidence(evidence_path)
        result = verify_and_persist_h0_offline_bound_state(
            state_path,
            root=root,
            tdd_evidence=evidence,
        )
        _safe_success(args.command, result)
        return

    if args.command == "authorize-q1-a":
        result = transition_q1_h0_a_live(
            state_path,
            root=root,
            dry_run=not args.commit,
        )
        _safe_success(args.command, result, committed=bool(args.commit))
        return

    if args.command == "revoke-h0-live":
        checkpoint_path = _resolve_under_root(root, args.checkpoint_index)
        checkpoint_relative = checkpoint_path.relative_to(root).as_posix()
        result = transition_h0_live_authorization_revoke(
            state_path,
            root=root,
            candidate_id=args.candidate_id,
            phase=args.phase,
            stage_attempt_id=args.attempt_id,
            checkpoint_index_path=checkpoint_relative,
            checkpoint_index_sha256=args.checkpoint_index_sha256,
            dry_run=not args.commit,
        )
        _safe_success(
            args.command,
            result,
            committed=bool(args.commit),
            reason="protocol_gate_order_violation",
            candidate_rerun_authorized=False,
            candidate_advance_authorized=False,
        )
        return

    if args.command == "run-q1-a":
        artifacts_path = _resolve_under_root(root, args.artifacts)
        result = asyncio.run(
            execute_h0_a_live(
                root=root,
                state_path=state_path,
                artifacts_root=artifacts_path,
                stage_attempt_id=args.attempt_id,
            )
        )
        _safe_success(args.command, result)
        return

    if args.command in {"run-q1-b", "run-q1-c"}:
        artifacts_path = _resolve_under_root(root, args.artifacts)
        result = asyncio.run(
            execute_h0_full_history_live(
                root=root,
                state_path=state_path,
                artifacts_root=artifacts_path,
                stage_attempt_id=args.attempt_id,
                candidate_id="Q1",
                phase=args.h0_phase,
            )
        )
        _safe_success(args.command, result)
        return

    raise H0ControlInputError("unknown_command")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one explicit control action and return a stable process exit code."""

    try:
        args = _build_parser().parse_args(argv)
    except H0ArgumentError:
        _safe_failure("argument_error")
        return 2
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    try:
        _run_command(args)
    except H0InfrastructureError as exc:
        reason = str(exc).split(":", 1)[0]
        if reason not in {
            "vllm_unreachable",
            "embedding_unreachable",
            "neo4j_unreachable",
        }:
            reason = "service_unreachable"
        _safe_failure(reason)
        return 75
    except H0ControlInputError:
        _safe_failure("control_input_invalid")
        return 20
    except H0StateGateError:
        _safe_failure("state_gate_denied")
        return 20
    except H0DataScopeError:
        _safe_failure("data_scope_failure")
        return 20
    except H0BudgetError:
        _safe_failure("context_budget_failure")
        return 20
    except H0SemanticError:
        _safe_failure("semantic_utility_failure")
        return 20
    except H0QualificationError:
        _safe_failure("candidate_qualification_failure")
        return 20
    except (
        H0ArtifactVerificationError,
        H0HarnessRecoveryError,
        H0RepairAdmissionError,
        H0PhaseStateError,
        H0ManifestError,
        H0StateTransitionError,
    ):
        _safe_failure("manifest_contract_failure")
        return 20
    except Exception:
        _safe_failure("unexpected_control_failure")
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
