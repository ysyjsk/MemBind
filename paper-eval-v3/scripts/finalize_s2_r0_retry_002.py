#!/usr/bin/env python3
"""Seal repaired S2-R0 replacement attempt 002 without live I/O."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s2_retrieval_probe import (
    corpus_identity_sha256,
    search_config_identity,
)
from paper_eval.s2_r0_authorization import (
    EXPECTED_EPISODE_COUNT,
    EXPECTED_HISTORY_ID,
    finalize_s2r0_authorization,
    finalize_s2r0_offline_qualification,
)
from paper_eval.s2_r0_controller import (
    RETRY_002_AUTHORIZATION,
    RETRY_002_CONSUMPTION,
    RETRY_002_QUALIFICATION,
    RETRY_002_RESULT,
    RETRY_002_RUN_ID,
    git_commit,
    production_dependencies,
    retry_002_binding_paths,
)


EXPECTED_PARENT_PROTOCOL_SHA256 = (
    "4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e"
)
EXPECTED_DATASET_SHA256 = (
    "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
)
EXPECTED_SPLIT_SHA256 = (
    "747946a8792422ea35e9d56b864efb1a137cb6eb8a8e16f97808fe86f938c091"
)
EXPECTED_CORPUS_SHA256 = (
    "fb54ffa48d426bab3b91f22e528dfc98f0f223d00432f49ace2d996a1b19c0fe"
)
EXPECTED_ORDERED_SESSION_IDS_SHA256 = (
    "658f87ebcd27d618d1dbdd7f119f375a4604a70dc76bcfe01150128f9e30e4ad"
)
EXPECTED_GOLD_SESSION_IDS_SHA256 = (
    "11ea72e3851677ba036823ace5fadd4a9d9f9e1ceff749fe6a949145b782d6e1"
)
EXPECTED_EPISODE_NAMES_SHA256 = (
    "5460f4b3bd0b6d61308294d09fcff906dac025b946ab3083ecefce7bd1149c96"
)
EXPECTED_EPISODE_CONTENT_SEQUENCE_SHA256 = (
    "56c9529d0e220a4de93cefdb4ee14e81e548c45169e8b0a5a1e03f447e116f0d"
)
EXPECTED_RETRIEVAL_CONFIG_SHA256 = (
    "411df587095daf9284ffaa8399a66886e88329999d934a26e28e0d43caad7d46"
)
EXPECTED_FIXED_BINDING_SHA256 = {
    "historical_s2_reference": (
        "0118ce9fbf288633df7405dad0570f1826665b61541b74cab813c3c3aba57f57"
    ),
    "s1_checkpoint": (
        "287de35d917ec45f43b9107b55b32aae0be4d513c16f06908b5d7b281ec8894e"
    ),
    "s1_events": (
        "7a9401f9fcf1d372854bea09dc6bd351c6f7af117463ddfd0bd399c12fabcffc"
    ),
    "historical_s2_checkpoint": (
        "bd231978613503aabfe895702de3f21f3c24c5ddd166944b6f37375d08f1f61d"
    ),
    "historical_s2_events": (
        "9dfaeafedf497992302614230d7afed75bdfb2c578f42a3f2459cf598b3240a0"
    ),
    "historical_s2_adapter_identity": (
        "3797aa87c66e2260fafaa4776863711801e67672e5182a3aa612b1e6b01962ec"
    ),
    "s2_contract_review": (
        "a3ee26a87cdebdb23c42e4827f3ac0ab8e7705ef96caeba2b7490e1350b1c848"
    ),
    "prior_s2r0_authorization": (
        "0a83291a4455013a5476e17ba3e9443eb9761ca55acd05b8fbd6a502f2be023a"
    ),
    "prior_s2r0_consumption": (
        "564e2ee43d7810280d40edefa3a9050e9b1025af974161e94482a07c182acb7d"
    ),
    "prior_s2r0_failure": (
        "f5709742e6f2209ebfa72d6b8d7b7566af7649774b34adc76819740cd40f71ff"
    ),
    "s2r0_failure_root_cause": (
        "324bd6b83e423e4421222ef057681e7a32b9ff7bccc39bb3972b0127f909a401"
    ),
    "retry_execution_plan": (
        "d436c5ad4e2196940a77f0eb7205ed47138c7f22e6c193f2bd94c28ca72fac76"
    ),
    "repair_red": (
        "03d9adfc3a74eeb71f7331a995ff577979692a101065fa9c670fa57665d3c002"
    ),
    "repair_targeted_green": (
        "a971c97e0e6c95c6a31b1b69a40bff89076ceb9b2f2e4c5f797de94039b3cd25"
    ),
    "repair_focused_green": (
        "7b3925df6de4f1c6704883fd74bf8a8e8427bd91f661e09901dbd3917d175e08"
    ),
    "repair_full_green": (
        "60090c69f1aa25490f642b3d041c3c89dd3e6d97c8752a40f4265a3bc59e68f9"
    ),
}


def _require_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise ValueError(f"S2-R0 retry 002 {label} drift")


def main() -> int:
    bindings = retry_002_binding_paths()
    dependencies = production_dependencies()
    history = dict(dependencies.load_history())
    episodes = list(dependencies.build_episodes(history))
    config = search_config_identity(dependencies.build_search_config())

    _require_equal(history.get("question_id"), EXPECTED_HISTORY_ID, "history")
    _require_equal(len(episodes), EXPECTED_EPISODE_COUNT, "episode count")
    session_ids = [str(value) for value in history.get("haystack_session_ids", [])]
    gold_ids = [str(value) for value in history.get("answer_session_ids", [])]
    _require_equal(
        [str(getattr(item, "session_id", "")) for item in episodes],
        session_ids,
        "episode/session mapping",
    )
    if len(set(session_ids)) != EXPECTED_EPISODE_COUNT or not set(gold_ids).issubset(
        session_ids
    ):
        raise ValueError("S2-R0 retry 002 frozen session set drift")

    identities = {
        "dataset": sha256_file(bindings["dataset"]),
        "split": sha256_file(bindings["frozen_split"]),
        "corpus": corpus_identity_sha256(episodes),
        "sessions": payload_sha256(session_ids),
        "gold": payload_sha256(gold_ids),
        "names": payload_sha256(
            [str(getattr(item, "name", "")) for item in episodes]
        ),
        "contents": payload_sha256(
            [
                hashlib.sha256(
                    str(getattr(item, "body", "")).encode("utf-8")
                ).hexdigest()
                for item in episodes
            ]
        ),
        "config": payload_sha256(config),
    }
    for name, expected in EXPECTED_FIXED_BINDING_SHA256.items():
        _require_equal(sha256_file(bindings[name]), expected, f"{name} binding")
    for actual, expected, label in (
        (
            sha256_file(bindings["parent_protocol"]),
            EXPECTED_PARENT_PROTOCOL_SHA256,
            "parent protocol",
        ),
        (identities["dataset"], EXPECTED_DATASET_SHA256, "dataset"),
        (identities["split"], EXPECTED_SPLIT_SHA256, "split"),
        (identities["corpus"], EXPECTED_CORPUS_SHA256, "corpus"),
        (identities["sessions"], EXPECTED_ORDERED_SESSION_IDS_SHA256, "sessions"),
        (identities["gold"], EXPECTED_GOLD_SESSION_IDS_SHA256, "gold"),
        (identities["names"], EXPECTED_EPISODE_NAMES_SHA256, "names"),
        (
            identities["contents"],
            EXPECTED_EPISODE_CONTENT_SEQUENCE_SHA256,
            "contents",
        ),
        (identities["config"], EXPECTED_RETRIEVAL_CONFIG_SHA256, "config"),
    ):
        _require_equal(actual, expected, label)

    lineage = {
        "prior_run_id": "s2r0-20260814-001",
        "replacement_run_id": RETRY_002_RUN_ID,
        "prior_authorization_sha256": sha256_file(
            bindings["prior_s2r0_authorization"]
        ),
        "prior_consumption_sha256": sha256_file(
            bindings["prior_s2r0_consumption"]
        ),
        "prior_failure_sha256": sha256_file(bindings["prior_s2r0_failure"]),
        "failure_classification": "HARNESS_QUERY_PARAMETER_NAME_COLLISION",
        "automatic_retry": False,
    }
    commit = git_commit()
    qualification = finalize_s2r0_offline_qualification(
        RETRY_002_QUALIFICATION,
        binding_paths=bindings,
        expected_parent_protocol_sha256=EXPECTED_PARENT_PROTOCOL_SHA256,
        retrieval_config_identity=config,
        dataset_sha256=identities["dataset"],
        frozen_split_sha256=identities["split"],
        frozen_corpus_identity_sha256=identities["corpus"],
        ordered_session_ids_sha256=identities["sessions"],
        gold_session_ids_sha256=identities["gold"],
        episode_names_sha256=identities["names"],
        episode_content_hash_sequence_sha256=identities["contents"],
        gold_session_count=len(gold_ids),
        git_commit=commit,
        run_id="s2r0-offline-qualification-20260814-002",
        retry_lineage=lineage,
    )
    authorization = finalize_s2r0_authorization(
        RETRY_002_AUTHORIZATION,
        qualification_path=RETRY_002_QUALIFICATION,
        binding_paths=bindings,
        expected_output_path=RETRY_002_RESULT,
        consumption_path=RETRY_002_CONSUMPTION,
        git_commit=commit,
        run_id=RETRY_002_RUN_ID,
    )
    print(
        json.dumps(
            {
                "authorization_path": str(RETRY_002_AUTHORIZATION),
                "authorization_sha256": sha256_file(RETRY_002_AUTHORIZATION),
                "qualification_path": str(RETRY_002_QUALIFICATION),
                "qualification_sha256": sha256_file(RETRY_002_QUALIFICATION),
                "qualification_verdict": qualification["payload"]["verdict"],
                "run_id": authorization["payload"]["run_id"],
                "status": "S2_R0_RETRY_002_ONE_SHOT_AUTHORIZED",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
