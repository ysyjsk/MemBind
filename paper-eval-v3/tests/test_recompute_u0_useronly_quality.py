"""TDD contracts for the read-only U0 quality-overlay reducer."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/recompute_u0_useronly_quality.py"
HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")


def _module():
    spec = importlib.util.spec_from_file_location("quality_overlay", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _item(history_id: str, qa: float, prompt_tokens: int) -> dict:
    return {
        "history_id": history_id,
        "status": "PASS",
        "quality_identity": {
            "baseline_id": "native-graphiti-u0-reader-v3-useronly",
            "reader_config_sha256": "1" * 64,
            "judge_config_sha256": "2" * 64,
            "useronly": True,
        },
        "quality": {
            "qa_accuracy": qa,
            "retrieval": {"evidence_recall_at_10": 1.0},
            "reader": {"prompt_tokens": prompt_tokens},
            "judge": {"status": "SUCCESS", "label": bool(qa)},
        },
    }


def test_summary_is_fixed_four_macro_quality_and_prompt_reduction() -> None:
    module = _module()
    items = [
        _item(history_id, qa, tokens)
        for history_id, qa, tokens in zip(
            HISTORIES,
            (1.0, 1.0, 1.0, 0.0),
            (3485, 7789, 4437, 4384),
            strict=True,
        )
    ]

    summary = module.summarize_overlay_items(
        items,
        legacy_prompt_tokens=(27843, 26282, 30925, 30634),
    )

    assert summary["qa_accuracy_macro"] == 0.75
    assert summary["evidence_recall_at_10_macro"] == 1.0
    assert summary["reader_prompt_tokens_total"] == 20095
    assert summary["legacy_reader_prompt_tokens_total"] == 115684
    assert summary["prompt_token_reduction_fraction"] == pytest.approx(
        1 - 20095 / 115684
    )
    assert summary["judge_sensitivity_status"] == (
        "QWEN_RUBRIC_HEADLINE_OFFICIAL_GPT4O_NOT_RUN"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows.reverse(),
        lambda rows: rows[0]["quality_identity"].update(useronly=False),
        lambda rows: rows[0]["quality_identity"].update(
            reader_config_sha256="3" * 64
        ),
        lambda rows: rows[0]["quality"]["judge"].update(status="INVALID"),
    ],
)
def test_summary_fails_closed_on_inventory_identity_or_judge_drift(mutate) -> None:
    module = _module()
    items = [_item(history_id, 1.0, 100) for history_id in HISTORIES]
    mutate(items)

    with pytest.raises(ValueError):
        module.summarize_overlay_items(
            items,
            legacy_prompt_tokens=(200, 200, 200, 200),
        )

