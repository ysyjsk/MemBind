"""Contract-first tests for the isolated MemBind v3.1 state-cut boundary.

These tests intentionally exercise only JSON-compatible value objects.  Live
Graphiti, model services, and Neo4j must not be import-time dependencies of the
certification boundary.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from paper_eval.membind_v31 import (
    CertificationError,
    CertificationRecord,
    DependencyClass,
    EffectClass,
    OperatorContract,
    OperatorContractError,
    PreparedArtifact,
    PreparedArtifactError,
    StateCutCertification,
)


HASHES = {name: f"{index:064x}" for index, name in enumerate(
    (
        "backend",
        "adapter",
        "operator",
        "code",
        "prompt",
        "schema",
        "config",
        "trace",
        "source",
        "evidence",
    ),
    start=1,
)}


def _contract(
    *,
    operator_name: str = "graphiti.extract_nodes",
    dependency_class: DependencyClass | str = DependencyClass.EVIDENCE_BOUND,
    effect_class: EffectClass | str = EffectClass.PURE,
) -> OperatorContract:
    return OperatorContract.create(
        operator_name=operator_name,
        dependency_class=dependency_class,
        effect_class=effect_class,
    )


def _certification(**overrides: object) -> CertificationRecord:
    values: dict[str, object] = {
        "operator_contract": _contract(),
        "memory_backend_identity_sha256": HASHES["backend"],
        "adapter_identity_sha256": HASHES["adapter"],
        "operator_identity_sha256": HASHES["operator"],
        "code_revision_sha256": HASHES["code"],
        "prompt_identity_sha256": HASHES["prompt"],
        "schema_identity_sha256": HASHES["schema"],
        "config_identity_sha256": HASHES["config"],
        "allowed_evidence_inputs": ("current_source", "evidence_snapshot"),
        "allowed_upstream_outputs": ("normalized_episode",),
        "allowed_apis": ("llm.generate_structured",),
        "forbidden_apis": (
            "graph_driver.execute_query",
            "memory.search",
            "memory.write",
        ),
        "qualification_trace_sha256": HASHES["trace"],
        "persistent_state_read_count": 0,
        "persistent_state_write_count": 0,
        "undeclared_external_side_effect_count": 0,
        "future_evidence_access_count": 0,
        "undeclared_state_facing_call_count": 0,
    }
    values.update(overrides)
    return CertificationRecord.create(**values)


def _artifact(**overrides: object) -> PreparedArtifact:
    values: dict[str, object] = {
        "source_sequence": 7,
        "source_sha256": HASHES["source"],
        "evidence_sha256": HASHES["evidence"],
        "certification_sha256": _certification().certification_sha256,
        "raw_nodes": (
            {"name": "Ada", "labels": ["Person"], "attributes": {"z": 2, "a": 1}},
            {"name": "Analytical Engine", "labels": ["Machine"]},
        ),
        "raw_edges": (
            {
                "source_name": "Ada",
                "target_name": "Analytical Engine",
                "relation_type": "DESCRIBED",
            },
        ),
    }
    values.update(overrides)
    return PreparedArtifact.create(**values)


@pytest.mark.parametrize(
    ("dependency_class", "effect_class"),
    (
        (DependencyClass.EVIDENCE_BOUND, EffectClass.PURE),
        (DependencyClass.STATE_BOUND, EffectClass.PURE),
        (DependencyClass.STATE_BOUND, EffectClass.STATE_READ),
        (DependencyClass.STATE_BOUND, EffectClass.STATE_WRITE),
        (DependencyClass.STATE_BOUND, EffectClass.PUBLISH),
    ),
)
def test_operator_contract_accepts_only_semantically_legal_class_pairs(
    dependency_class: DependencyClass,
    effect_class: EffectClass,
) -> None:
    contract = _contract(
        dependency_class=dependency_class,
        effect_class=effect_class,
    )

    assert contract.dependency_class is dependency_class
    assert contract.effect_class is effect_class
    assert contract.verify() is contract
    assert len(contract.contract_sha256) == 64


@pytest.mark.parametrize(
    "effect_class",
    (EffectClass.STATE_READ, EffectClass.STATE_WRITE, EffectClass.PUBLISH),
)
def test_evidence_bound_operator_rejects_any_mutable_state_effect(
    effect_class: EffectClass,
) -> None:
    with pytest.raises(OperatorContractError, match="evidence_bound_must_be_pure"):
        _contract(effect_class=effect_class)


@pytest.mark.parametrize(
    ("dependency_class", "effect_class", "error"),
    (
        ("UNKNOWN", EffectClass.PURE, "dependency_class_invalid"),
        (DependencyClass.STATE_BOUND, "UNKNOWN", "effect_class_invalid"),
        (DependencyClass.EVIDENCE_BOUND, True, "effect_class_invalid"),
    ),
)
def test_operator_contract_rejects_unknown_or_coerced_enum_values(
    dependency_class: object,
    effect_class: object,
    error: str,
) -> None:
    with pytest.raises(OperatorContractError, match=error):
        _contract(dependency_class=dependency_class, effect_class=effect_class)


def test_certification_binds_complete_identity_and_canonical_api_sets() -> None:
    record = _certification(
        allowed_apis=("llm.generate_structured", "episode.normalize", "llm.generate_structured"),
        forbidden_apis=("memory.write", "memory.search", "graph_driver.execute_query"),
    )

    assert record.allowed_apis == ("episode.normalize", "llm.generate_structured")
    assert record.forbidden_apis == (
        "graph_driver.execute_query",
        "memory.search",
        "memory.write",
    )
    assert record.forbidden_counts == {
        "future_evidence_access_count": 0,
        "persistent_state_read_count": 0,
        "persistent_state_write_count": 0,
        "undeclared_external_side_effect_count": 0,
        "undeclared_state_facing_call_count": 0,
    }
    assert record.verify() is record
    assert record.payload()["operator_contract_sha256"] == record.operator_contract_sha256
    assert len(record.certification_sha256) == 64


@pytest.mark.parametrize(
    "counter",
    (
        "persistent_state_read_count",
        "persistent_state_write_count",
        "undeclared_external_side_effect_count",
        "future_evidence_access_count",
        "undeclared_state_facing_call_count",
    ),
)
def test_certification_fails_closed_when_any_forbidden_count_is_nonzero(
    counter: str,
) -> None:
    with pytest.raises(CertificationError, match="state_cut_certification_failure"):
        _certification(**{counter: 1})


def test_certification_rejects_non_evidence_bound_or_non_pure_operator() -> None:
    with pytest.raises(CertificationError, match="operator_not_compile_eligible"):
        _certification(
            operator_contract=_contract(
                dependency_class=DependencyClass.STATE_BOUND,
                effect_class=EffectClass.STATE_READ,
            )
        )


def test_certification_rejects_ambiguous_api_policy_or_bad_identity() -> None:
    with pytest.raises(CertificationError, match="api_policy_overlap"):
        _certification(allowed_apis=("memory.search",))
    with pytest.raises(CertificationError, match="code_revision_sha256_invalid"):
        _certification(code_revision_sha256="not-a-sha256")


def test_certification_hash_covers_every_identity_and_trace_policy_field() -> None:
    baseline = _certification()
    variants = (
        _certification(memory_backend_identity_sha256="a" * 64),
        _certification(adapter_identity_sha256="b" * 64),
        _certification(operator_identity_sha256="c" * 64),
        _certification(code_revision_sha256="d" * 64),
        _certification(prompt_identity_sha256="e" * 64),
        _certification(schema_identity_sha256="f" * 64),
        _certification(config_identity_sha256="0" * 64),
        _certification(allowed_evidence_inputs=("current_source",)),
        _certification(allowed_upstream_outputs=()),
        _certification(allowed_apis=("episode.normalize",)),
        _certification(forbidden_apis=("memory.search",)),
        _certification(qualification_trace_sha256="9" * 64),
    )

    assert all(item.certification_sha256 != baseline.certification_sha256 for item in variants)


def test_direct_certification_tamper_is_detected_by_self_verification() -> None:
    record = _certification()
    tampered = replace(record, qualification_trace_sha256="f" * 64)

    with pytest.raises(CertificationError, match="certification_hash_mismatch"):
        tampered.verify()


def test_state_cut_certification_canonicalizes_multiple_operator_records() -> None:
    node = _certification(
        operator_contract=_contract(operator_name="graphiti.extract_nodes"),
    )
    edge = _certification(
        operator_contract=_contract(operator_name="graphiti.extract_edges"),
        operator_identity_sha256="a" * 64,
        prompt_identity_sha256="b" * 64,
    )

    first = StateCutCertification.create([edge, node])
    second = StateCutCertification.create([node, edge])

    assert first.operator_names == (
        "graphiti.extract_edges",
        "graphiti.extract_nodes",
    )
    assert first.certification_sha256 == second.certification_sha256
    assert first.verify() is first


def test_state_cut_certification_fails_closed_on_duplicate_or_tamper() -> None:
    record = _certification()
    with pytest.raises(CertificationError, match="certification_operator_duplicate"):
        StateCutCertification.create([record, record])

    bundle = StateCutCertification.create([record])
    tampered = replace(bundle, certification_sha256="f" * 64)
    with pytest.raises(CertificationError, match="state_cut_certification_hash_mismatch"):
        tampered.verify()


def test_prepared_artifact_canonicalizes_raw_nodes_and_optional_edges() -> None:
    first = _artifact()
    same = _artifact(
        raw_nodes=(
            {"attributes": {"a": 1, "z": 2}, "labels": ["Person"], "name": "Ada"},
            {"labels": ["Machine"], "name": "Analytical Engine"},
        ),
    )
    nodes_only = _artifact(raw_edges=None)

    assert first.raw_nodes == same.raw_nodes
    assert first.artifact_sha256 == same.artifact_sha256
    assert nodes_only.raw_edges is None
    assert nodes_only.payload()["raw_edges"] is None
    assert first.verify() is first


def test_prepared_artifact_hash_binds_source_evidence_certification_and_raw_data() -> None:
    baseline = _artifact()
    variants = (
        _artifact(source_sha256="a" * 64),
        _artifact(evidence_sha256="b" * 64),
        _artifact(certification_sha256="c" * 64),
        _artifact(raw_nodes=({"name": "Grace"},)),
        _artifact(raw_edges=None),
    )

    assert all(item.artifact_sha256 != baseline.artifact_sha256 for item in variants)


def test_prepared_artifact_rejects_conflicting_expected_identity() -> None:
    artifact = _artifact()

    with pytest.raises(PreparedArtifactError, match="source_identity_conflict"):
        artifact.verify(expected_source_sha256="f" * 64)
    with pytest.raises(PreparedArtifactError, match="evidence_identity_conflict"):
        artifact.verify(expected_evidence_sha256="e" * 64)
    with pytest.raises(PreparedArtifactError, match="certification_identity_conflict"):
        artifact.verify(expected_certification_sha256="d" * 64)


def test_prepared_artifact_detects_tamper_and_returns_defensive_json_copies() -> None:
    artifact = _artifact()
    external_nodes = artifact.raw_nodes
    external_nodes[0]["name"] = "tampered outside"
    assert artifact.raw_nodes[0]["name"] == "Ada"

    tampered = replace(artifact, source_sha256="f" * 64)
    with pytest.raises(PreparedArtifactError, match="artifact_hash_mismatch"):
        tampered.verify()


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("source_sequence", -1, "source_sequence_invalid"),
        ("raw_nodes", "not-a-sequence-of-mappings", "raw_nodes_invalid"),
        ("raw_nodes", ({"bad": object()},), "raw_nodes_invalid"),
        ("raw_edges", ("not-a-mapping",), "raw_edges_invalid"),
    ),
)
def test_prepared_artifact_rejects_noncanonical_or_malformed_input(
    field: str,
    value: object,
    error: str,
) -> None:
    with pytest.raises(PreparedArtifactError, match=error):
        _artifact(**{field: value})


def test_prepared_artifact_binds_pure_intermediates_defensively() -> None:
    artifact = _artifact(
        pure_intermediates={"node_episode_index_map": {"node-a": [0, 2]}},
    )

    selected = artifact.pure_intermediates
    selected["node_episode_index_map"]["node-a"].append(99)

    assert artifact.pure_intermediates == {
        "node_episode_index_map": {"node-a": [0, 2]}
    }
    assert artifact.verify() is artifact
    assert artifact.to_document()["pure_intermediates"] == {
        "node_episode_index_map": {"node-a": [0, 2]}
    }


def test_prepared_artifact_rejects_tampered_pure_intermediates() -> None:
    artifact = _artifact(
        pure_intermediates={"node_episode_index_map": {}},
    )
    tampered = replace(
        artifact,
        _pure_intermediates_json='{"node_episode_index_map":{"leak":[7]}}',
    )

    with pytest.raises(PreparedArtifactError, match="artifact_hash_mismatch"):
        tampered.verify()
