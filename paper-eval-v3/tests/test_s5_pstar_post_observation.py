"""TDD for full and partial P* terminal namespace accounting."""

import copy
import pytest

from paper_eval.s5_pstar_post_observation import (
    S5PStarPostObservationError,
    build_s5_pstar_post_observation,
    verify_s5_pstar_post_observation,
)

SOURCES = [{"source_sequence": i, "source_sha256": f"{i + 1:064x}"} for i in range(49)]


def _terminal(i, kind):
    return {**SOURCES[i], "terminal_classification": kind}


def test_full_publication_retains_direct_invariant_violation():
    artifact = build_s5_pstar_post_observation(
        run_id="s5-p-star-20260816-111", expected_sources=SOURCES,
        source_terminals=[_terminal(i, "PUBLISHED") for i in range(49)],
        observed_episodics=SOURCES,
        violation_counts={"entity_namespace_escape_count": 2},
        per_source_violation_counts={str(i): (2 if i == 0 else 0) for i in range(49)},
    )
    checked = verify_s5_pstar_post_observation(artifact)
    assert checked["status"] == "DIRECT_INVARIANT_VIOLATION_OBSERVED"
    assert checked["accounting"] == {"expected": 49, "published": 49, "failed": 0, "censored": 0}


def test_partial_treatment_failure_observes_exact_published_subset():
    terminals = [_terminal(i, "PUBLISHED") for i in range(3)]
    terminals += [_terminal(3, "TREATMENT_FAILED")]
    terminals += [_terminal(i, "CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE") for i in range(4, 49)]
    checked = verify_s5_pstar_post_observation(build_s5_pstar_post_observation(
        run_id="s5-p-star-20260816-112", expected_sources=SOURCES,
        source_terminals=terminals, observed_episodics=SOURCES[:3],
        violation_counts={},
        per_source_violation_counts={str(i): 0 for i in range(3)},
    ))
    assert checked["status"] == "TREATMENT_FAILURE_OBSERVED"
    assert checked["accounting"] == {"expected": 49, "published": 3, "failed": 1, "censored": 45}


@pytest.mark.parametrize("mutation", ["missing_terminal", "extra_episode", "missing_episode"])
def test_incomplete_or_mismatched_partial_evidence_fails_closed(mutation):
    terminals = [_terminal(i, "PUBLISHED") for i in range(2)] + [_terminal(2, "TREATMENT_FAILED")]
    terminals += [_terminal(i, "CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE") for i in range(3, 49)]
    episodes = copy.deepcopy(SOURCES[:2])
    if mutation == "missing_terminal": terminals.pop()
    if mutation == "extra_episode": episodes.append(SOURCES[3])
    if mutation == "missing_episode": episodes.pop()
    with pytest.raises(S5PStarPostObservationError):
        build_s5_pstar_post_observation(
            run_id="s5-p-star-20260816-113", expected_sources=SOURCES,
            source_terminals=terminals, observed_episodics=episodes,
            violation_counts={},
            per_source_violation_counts={str(i): 0 for i in range(2)},
        )


def test_per_source_violation_coverage_must_match_published_sources():
    with pytest.raises(S5PStarPostObservationError):
        build_s5_pstar_post_observation(
            run_id="s5-p-star-20260816-114",
            expected_sources=SOURCES,
            source_terminals=[_terminal(i, "PUBLISHED") for i in range(49)],
            observed_episodics=SOURCES,
            violation_counts={},
            per_source_violation_counts={str(i): 0 for i in range(48)},
        )
