#!/usr/bin/env python3
"""Seal the offline S2-R0 qualification and its one-shot authorization."""

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
    DEFAULT_AUTHORIZATION,
    DEFAULT_CONSUMPTION,
    DEFAULT_QUALIFICATION,
    DEFAULT_RESULT,
    DEFAULT_RUN_ID,
    git_commit,
    production_binding_paths,
    production_dependencies,
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
EXPECTED_HISTORICAL_BINDING_SHA256 = {
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
}


def _require_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise ValueError(f"S2-R0 {label} drift")


def main() -> int:
    """Compute identities from frozen inputs, then seal without live I/O."""

    bindings = production_binding_paths()
    dependencies = production_dependencies()
    history = dict(dependencies.load_history())
    episodes = list(dependencies.build_episodes(history))
    config = search_config_identity(dependencies.build_search_config())

    _require_equal(history.get("question_id"), EXPECTED_HISTORY_ID, "history identity")
    _require_equal(len(episodes), EXPECTED_EPISODE_COUNT, "episode count")
    session_ids = [str(value) for value in history.get("haystack_session_ids", [])]
    gold_ids = [str(value) for value in history.get("answer_session_ids", [])]
    episode_session_ids = [str(getattr(item, "session_id", "")) for item in episodes]
    _require_equal(episode_session_ids, session_ids, "episode/session mapping")
    if len(set(session_ids)) != EXPECTED_EPISODE_COUNT or not set(gold_ids).issubset(
        session_ids
    ):
        raise ValueError("S2-R0 frozen session set drift")

    dataset_sha256 = sha256_file(bindings["dataset"])
    split_sha256 = sha256_file(bindings["frozen_split"])
    corpus_sha256 = corpus_identity_sha256(episodes)
    session_ids_sha256 = payload_sha256(session_ids)
    gold_ids_sha256 = payload_sha256(gold_ids)
    names_sha256 = payload_sha256(
        [str(getattr(item, "name", "")) for item in episodes]
    )
    content_sequence_sha256 = payload_sha256(
        [
            hashlib.sha256(
                str(getattr(item, "body", "")).encode("utf-8")
            ).hexdigest()
            for item in episodes
        ]
    )
    config_sha256 = payload_sha256(config)

    for name, expected_sha256 in EXPECTED_HISTORICAL_BINDING_SHA256.items():
        _require_equal(
            sha256_file(bindings[name]), expected_sha256, f"{name} binding"
        )
    for actual, expected, label in (
        (
            sha256_file(bindings["parent_protocol"]),
            EXPECTED_PARENT_PROTOCOL_SHA256,
            "parent protocol",
        ),
        (dataset_sha256, EXPECTED_DATASET_SHA256, "dataset"),
        (split_sha256, EXPECTED_SPLIT_SHA256, "frozen split"),
        (corpus_sha256, EXPECTED_CORPUS_SHA256, "frozen corpus"),
        (session_ids_sha256, EXPECTED_ORDERED_SESSION_IDS_SHA256, "ordered sessions"),
        (gold_ids_sha256, EXPECTED_GOLD_SESSION_IDS_SHA256, "gold sessions"),
        (names_sha256, EXPECTED_EPISODE_NAMES_SHA256, "episode names"),
        (
            content_sequence_sha256,
            EXPECTED_EPISODE_CONTENT_SEQUENCE_SHA256,
            "episode content sequence",
        ),
        (config_sha256, EXPECTED_RETRIEVAL_CONFIG_SHA256, "retrieval config"),
    ):
        _require_equal(actual, expected, label)

    commit = git_commit()
    qualification = finalize_s2r0_offline_qualification(
        DEFAULT_QUALIFICATION,
        binding_paths=bindings,
        expected_parent_protocol_sha256=EXPECTED_PARENT_PROTOCOL_SHA256,
        retrieval_config_identity=config,
        dataset_sha256=dataset_sha256,
        frozen_split_sha256=split_sha256,
        frozen_corpus_identity_sha256=corpus_sha256,
        ordered_session_ids_sha256=session_ids_sha256,
        gold_session_ids_sha256=gold_ids_sha256,
        episode_names_sha256=names_sha256,
        episode_content_hash_sequence_sha256=content_sequence_sha256,
        gold_session_count=len(gold_ids),
        git_commit=commit,
        run_id="s2r0-offline-qualification-20260814-001",
    )
    authorization = finalize_s2r0_authorization(
        DEFAULT_AUTHORIZATION,
        qualification_path=DEFAULT_QUALIFICATION,
        binding_paths=bindings,
        expected_output_path=DEFAULT_RESULT,
        consumption_path=DEFAULT_CONSUMPTION,
        git_commit=commit,
        run_id=DEFAULT_RUN_ID,
    )
    print(
        json.dumps(
            {
                "authorization_path": str(DEFAULT_AUTHORIZATION),
                "authorization_sha256": sha256_file(DEFAULT_AUTHORIZATION),
                "qualification_path": str(DEFAULT_QUALIFICATION),
                "qualification_sha256": sha256_file(DEFAULT_QUALIFICATION),
                "qualification_verdict": qualification["payload"]["verdict"],
                "run_id": authorization["payload"]["run_id"],
                "status": "S2_R0_ONE_SHOT_AUTHORIZED",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
