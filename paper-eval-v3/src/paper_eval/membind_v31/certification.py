"""Fail-closed certification records for MemBind v3.1 Compile operators."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.contracts import OperatorContract


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COUNTER_NAMES = (
    "future_evidence_access_count",
    "persistent_state_read_count",
    "persistent_state_write_count",
    "undeclared_external_side_effect_count",
    "undeclared_state_facing_call_count",
)


class CertificationError(ValueError):
    """A qualification record is invalid or fails the certified state cut."""


def _fail(code: str) -> CertificationError:
    return CertificationError(code)


def _sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _counter(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _canonical_names(value: object, code: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise _fail(code)
    try:
        selected = tuple(value)  # type: ignore[arg-type]
    except TypeError:
        raise _fail(code) from None
    if any(
        not isinstance(item, str) or not item.strip() or item != item.strip()
        for item in selected
    ):
        raise _fail(code)
    return tuple(sorted(set(selected)))


@dataclass(frozen=True, slots=True)
class CertificationRecord:
    """A complete, self-hashing proof record for one Compile operator.

    Construction succeeds only when the declared operator is EVIDENCE_BOUND /
    PURE and every forbidden observation count is exactly zero.
    """

    operator_contract: OperatorContract
    memory_backend_identity_sha256: str
    adapter_identity_sha256: str
    operator_identity_sha256: str
    code_revision_sha256: str
    prompt_identity_sha256: str
    schema_identity_sha256: str
    config_identity_sha256: str
    allowed_evidence_inputs: tuple[str, ...]
    allowed_upstream_outputs: tuple[str, ...]
    allowed_apis: tuple[str, ...]
    forbidden_apis: tuple[str, ...]
    qualification_trace_sha256: str
    persistent_state_read_count: int
    persistent_state_write_count: int
    undeclared_external_side_effect_count: int
    future_evidence_access_count: int
    undeclared_state_facing_call_count: int
    certification_sha256: str

    @classmethod
    def create(
        cls,
        *,
        operator_contract: OperatorContract,
        memory_backend_identity_sha256: str,
        adapter_identity_sha256: str,
        operator_identity_sha256: str,
        code_revision_sha256: str,
        prompt_identity_sha256: str,
        schema_identity_sha256: str,
        config_identity_sha256: str,
        allowed_evidence_inputs: object,
        allowed_upstream_outputs: object,
        allowed_apis: object,
        forbidden_apis: object,
        qualification_trace_sha256: str,
        persistent_state_read_count: int,
        persistent_state_write_count: int,
        undeclared_external_side_effect_count: int,
        future_evidence_access_count: int,
        undeclared_state_facing_call_count: int,
    ) -> "CertificationRecord":
        if not isinstance(operator_contract, OperatorContract):
            raise _fail("operator_contract_invalid")
        try:
            contract = operator_contract.verify()
        except ValueError:
            raise _fail("operator_contract_invalid") from None
        if not contract.compile_eligible:
            raise _fail("operator_not_compile_eligible")

        identities = {
            "memory_backend_identity_sha256": _sha256(
                memory_backend_identity_sha256,
                "memory_backend_identity_sha256_invalid",
            ),
            "adapter_identity_sha256": _sha256(
                adapter_identity_sha256, "adapter_identity_sha256_invalid"
            ),
            "operator_identity_sha256": _sha256(
                operator_identity_sha256, "operator_identity_sha256_invalid"
            ),
            "code_revision_sha256": _sha256(
                code_revision_sha256, "code_revision_sha256_invalid"
            ),
            "prompt_identity_sha256": _sha256(
                prompt_identity_sha256, "prompt_identity_sha256_invalid"
            ),
            "schema_identity_sha256": _sha256(
                schema_identity_sha256, "schema_identity_sha256_invalid"
            ),
            "config_identity_sha256": _sha256(
                config_identity_sha256, "config_identity_sha256_invalid"
            ),
        }
        policies = {
            "allowed_evidence_inputs": _canonical_names(
                allowed_evidence_inputs, "allowed_evidence_inputs_invalid"
            ),
            "allowed_upstream_outputs": _canonical_names(
                allowed_upstream_outputs, "allowed_upstream_outputs_invalid"
            ),
            "allowed_apis": _canonical_names(allowed_apis, "allowed_apis_invalid"),
            "forbidden_apis": _canonical_names(forbidden_apis, "forbidden_apis_invalid"),
        }
        if set(policies["allowed_apis"]) & set(policies["forbidden_apis"]):
            raise _fail("api_policy_overlap")
        counters = {
            "persistent_state_read_count": _counter(
                persistent_state_read_count, "persistent_state_read_count_invalid"
            ),
            "persistent_state_write_count": _counter(
                persistent_state_write_count, "persistent_state_write_count_invalid"
            ),
            "undeclared_external_side_effect_count": _counter(
                undeclared_external_side_effect_count,
                "undeclared_external_side_effect_count_invalid",
            ),
            "future_evidence_access_count": _counter(
                future_evidence_access_count, "future_evidence_access_count_invalid"
            ),
            "undeclared_state_facing_call_count": _counter(
                undeclared_state_facing_call_count,
                "undeclared_state_facing_call_count_invalid",
            ),
        }
        if any(counters.values()):
            raise _fail("state_cut_certification_failure")
        trace = _sha256(
            qualification_trace_sha256, "qualification_trace_sha256_invalid"
        )
        payload = cls._payload_from_parts(
            operator_contract_sha256=contract.contract_sha256,
            identities=identities,
            policies=policies,
            qualification_trace_sha256=trace,
            counters=counters,
        )
        return cls(
            operator_contract=contract,
            qualification_trace_sha256=trace,
            certification_sha256=payload_sha256(payload),
            **identities,
            **policies,
            **counters,
        )

    @staticmethod
    def _payload_from_parts(
        *,
        operator_contract_sha256: str,
        identities: dict[str, str],
        policies: dict[str, tuple[str, ...]],
        qualification_trace_sha256: str,
        counters: dict[str, int],
    ) -> dict[str, object]:
        return {
            **identities,
            **{name: list(values) for name, values in policies.items()},
            **counters,
            "operator_contract_sha256": operator_contract_sha256,
            "qualification_trace_sha256": qualification_trace_sha256,
        }

    @property
    def operator_contract_sha256(self) -> str:
        return self.operator_contract.contract_sha256

    @property
    def forbidden_counts(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in _COUNTER_NAMES}

    def payload(self) -> dict[str, object]:
        return self._payload_from_parts(
            operator_contract_sha256=self.operator_contract_sha256,
            identities={
                "memory_backend_identity_sha256": self.memory_backend_identity_sha256,
                "adapter_identity_sha256": self.adapter_identity_sha256,
                "operator_identity_sha256": self.operator_identity_sha256,
                "code_revision_sha256": self.code_revision_sha256,
                "prompt_identity_sha256": self.prompt_identity_sha256,
                "schema_identity_sha256": self.schema_identity_sha256,
                "config_identity_sha256": self.config_identity_sha256,
            },
            policies={
                "allowed_evidence_inputs": self.allowed_evidence_inputs,
                "allowed_upstream_outputs": self.allowed_upstream_outputs,
                "allowed_apis": self.allowed_apis,
                "forbidden_apis": self.forbidden_apis,
            },
            qualification_trace_sha256=self.qualification_trace_sha256,
            counters={
                "persistent_state_read_count": self.persistent_state_read_count,
                "persistent_state_write_count": self.persistent_state_write_count,
                "undeclared_external_side_effect_count": self.undeclared_external_side_effect_count,
                "future_evidence_access_count": self.future_evidence_access_count,
                "undeclared_state_facing_call_count": self.undeclared_state_facing_call_count,
            },
        )

    def verify(self) -> "CertificationRecord":
        recreated = self.create(
            operator_contract=self.operator_contract,
            memory_backend_identity_sha256=self.memory_backend_identity_sha256,
            adapter_identity_sha256=self.adapter_identity_sha256,
            operator_identity_sha256=self.operator_identity_sha256,
            code_revision_sha256=self.code_revision_sha256,
            prompt_identity_sha256=self.prompt_identity_sha256,
            schema_identity_sha256=self.schema_identity_sha256,
            config_identity_sha256=self.config_identity_sha256,
            allowed_evidence_inputs=self.allowed_evidence_inputs,
            allowed_upstream_outputs=self.allowed_upstream_outputs,
            allowed_apis=self.allowed_apis,
            forbidden_apis=self.forbidden_apis,
            qualification_trace_sha256=self.qualification_trace_sha256,
            persistent_state_read_count=self.persistent_state_read_count,
            persistent_state_write_count=self.persistent_state_write_count,
            undeclared_external_side_effect_count=self.undeclared_external_side_effect_count,
            future_evidence_access_count=self.future_evidence_access_count,
            undeclared_state_facing_call_count=self.undeclared_state_facing_call_count,
        )
        _sha256(self.certification_sha256, "certification_sha256_invalid")
        if recreated.certification_sha256 != self.certification_sha256:
            raise _fail("certification_hash_mismatch")
        return self


@dataclass(frozen=True, slots=True)
class StateCutCertification:
    """Canonical certification identity for the complete Compile region."""

    _records: tuple[CertificationRecord, ...]
    certification_sha256: str

    @classmethod
    def create(
        cls,
        records: Sequence[CertificationRecord],
    ) -> "StateCutCertification":
        if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
            raise _fail("certification_records_invalid")
        selected = tuple(records)
        if not selected or any(not isinstance(item, CertificationRecord) for item in selected):
            raise _fail("certification_records_invalid")
        for record in selected:
            record.verify()
        ordered = tuple(
            sorted(selected, key=lambda item: item.operator_contract.operator_name)
        )
        names = tuple(item.operator_contract.operator_name for item in ordered)
        if len(set(names)) != len(names):
            raise _fail("certification_operator_duplicate")
        return cls(
            _records=ordered,
            certification_sha256=payload_sha256(cls._payload(ordered)),
        )

    @staticmethod
    def _payload(records: tuple[CertificationRecord, ...]) -> dict[str, object]:
        return {
            "operators": [
                {
                    "certification_sha256": record.certification_sha256,
                    "operator_name": record.operator_contract.operator_name,
                }
                for record in records
            ]
        }

    @property
    def records(self) -> tuple[CertificationRecord, ...]:
        return self._records

    @property
    def operator_names(self) -> tuple[str, ...]:
        return tuple(record.operator_contract.operator_name for record in self._records)

    def payload(self) -> dict[str, object]:
        return self._payload(self._records)

    def verify(self) -> "StateCutCertification":
        recreated = self.create(self._records)
        _sha256(self.certification_sha256, "state_cut_certification_sha256_invalid")
        if recreated.certification_sha256 != self.certification_sha256:
            raise _fail("state_cut_certification_hash_mismatch")
        return self


__all__ = ["CertificationError", "CertificationRecord", "StateCutCertification"]
