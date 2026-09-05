from dataclasses import replace

import pytest

from membind.core.contracts import (
    PreparedWork,
    PreparedWorkStore,
    RequestIdentity,
    canonical_sha256,
    validate_prepared_work,
)


def identity(logical_id="e0"):
    return RequestIdentity(
        logical_id=logical_id,
        method="NATIVE",
        episode_sha256="episode",
        previous_state_sha256="state",
        model_identity="model",
        graphiti_identity="graphiti",
        schema_sha256="schema",
        config_sha256="config",
    )


def test_prepared_work_is_valid_only_for_exact_identity():
    expected = identity()
    work = PreparedWork(expected, {"nodes": [1]}, "test", 1)
    assert validate_prepared_work(work, expected).valid
    assert validate_prepared_work(work, replace(expected, previous_state_sha256="changed")).reason == "REQUEST_IDENTITY_MISMATCH"


def test_store_is_one_shot_and_rejects_duplicate_logical_ids():
    store = PreparedWorkStore()
    work = PreparedWork(identity(), "payload", "test", 1)
    store.put(work)
    with pytest.raises(ValueError, match="already exists"):
        store.put(work)
    assert store.pop("e0") == work
    assert store.pop("e0") is None


def test_payload_digest_is_checked():
    expected = identity()
    work = PreparedWork(expected, {"x": 1}, "test", 1)
    forged = object.__new__(PreparedWork)
    object.__setattr__(forged, "identity", work.identity)
    object.__setattr__(forged, "payload", {"x": 2})
    object.__setattr__(forged, "producer", work.producer)
    object.__setattr__(forged, "created_ns", work.created_ns)
    object.__setattr__(forged, "payload_sha256", work.payload_sha256)
    assert validate_prepared_work(forged, expected).reason == "PAYLOAD_DIGEST_MISMATCH"
    assert canonical_sha256({"x": 1}) == work.payload_sha256
