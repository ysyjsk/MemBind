"""RED contracts for commit-completed/publication-missing recovery."""

from __future__ import annotations

from pathlib import Path
import json

import pytest

from paper_eval.s5_mstar_publication_journal import (
    S5MStarPublicationJournal,
    S5MStarPublicationJournalError,
)


OP = "a" * 64
SOURCE = "b" * 64
COMMIT = "c" * 64


def test_commit_then_missing_publication_recovers_once_after_reload(tmp_path: Path) -> None:
    journal = S5MStarPublicationJournal.create(tmp_path / "journal.jsonl")
    journal.record_intent(OP, SOURCE)
    journal.record_commit(OP, COMMIT)
    reloaded = S5MStarPublicationJournal.load(journal.path)
    assert reloaded.recover_publication(OP, lambda: True) == "RECOVERED_PUBLICATION"
    assert reloaded.recover_publication(OP, lambda: True) == "ALREADY_PUBLISHED"
    assert reloaded.published_operations() == (OP,)


def test_duplicate_commit_and_publication_are_idempotent_but_conflict_fails_closed(
    tmp_path: Path,
) -> None:
    journal = S5MStarPublicationJournal.create(tmp_path / "journal.jsonl")
    journal.record_intent(OP, SOURCE)
    assert journal.record_commit(OP, COMMIT) == "COMMITTED"
    assert journal.record_commit(OP, COMMIT) == "ALREADY_COMMITTED"
    assert journal.record_publication(OP) == "PUBLISHED"
    assert journal.record_publication(OP) == "ALREADY_PUBLISHED"
    with pytest.raises(S5MStarPublicationJournalError, match="commit_conflict"):
        journal.record_commit(OP, "d" * 64)


def test_recovery_requires_probe_to_confirm_external_commit(tmp_path: Path) -> None:
    journal = S5MStarPublicationJournal.create(tmp_path / "journal.jsonl")
    journal.record_intent(OP, SOURCE)
    journal.record_commit(OP, COMMIT)
    with pytest.raises(S5MStarPublicationJournalError, match="commit_not_confirmed"):
        journal.recover_publication(OP, lambda: False)


def test_reloaded_journal_rejects_rehashed_event_shape_tampering(tmp_path: Path) -> None:
    journal = S5MStarPublicationJournal.create(tmp_path / "journal.jsonl")
    journal.record_intent(OP, SOURCE)
    raw = json.loads(journal.path.read_text(encoding="utf-8"))
    raw["event"]["event_sequence"] = 1
    from paper_eval.artifacts import payload_sha256

    raw["event_sha256"] = payload_sha256(raw["event"])
    journal.path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    with pytest.raises(S5MStarPublicationJournalError, match="intent_conflict|sequence"):
        S5MStarPublicationJournal.load(journal.path)
