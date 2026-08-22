"""P1 certificate checks kept small and fail-closed."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from ..runtime.adapters.graphiti_0293 import Graphiti0293Adapter
from ..runtime.core.contracts import HoistCertificate, PreviousSourceProjector


def build_dependency_certificate(*, adapter: Graphiti0293Adapter | None = None) -> dict[str, Any]:
    selected = adapter or Graphiti0293Adapter.inspect_installed()
    selected.assert_expected()
    certificate = selected.certificate()
    result = selected.to_dict()
    result["certificate"] = certificate.to_dict()
    result["status"] = "PASS"
    return result


def validate_certificate_fixture(
    certificate: HoistCertificate,
    source_prefix: list[Mapping[str, Any]],
    *,
    sequence: int,
    valid_at: datetime | None = None,
) -> dict[str, Any]:
    certificate.validate()
    current = valid_at or datetime.now(timezone.utc)
    projector = PreviousSourceProjector(source_prefix)
    projected = projector.project(sequence=sequence, valid_at=current)
    return {
        "status": "PASS",
        "certificate_digest": certificate.digest(),
        "projected_count": len(projected),
        "source_closure": True,
        "normal_control_closed": True,
        "exception_abort_policy": certificate.abort_policy,
    }

