"""Process-local evidence for persistent transaction commit epochs.

``MemoryVersionToken`` identifies a logical published memory version.  A
mutation epoch has a narrower purpose: it advances after every successful
persistent transaction, including transactions that do not publish a source.
The counter is read before and after a multi-query semantic read so a capture
that crosses any observed commit fails closed.

This module does not perform a transaction and does not import a backend.  A
runtime adapter must call :meth:`StateMutationEpoch.record_commit` only after
its real commit has returned successfully.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import Lock


class MutationEpochError(ValueError):
    """Mutation epoch evidence is malformed or contradictory."""


def _fail(code: str) -> MutationEpochError:
    return MutationEpochError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _counter(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("mutation_epoch_counter_invalid")
    return value


@dataclass(frozen=True, slots=True)
class MutationEpochToken:
    """One observed position in a backend namespace's commit sequence."""

    namespace: str
    backend_id: str
    epoch: str
    counter: int
    transaction_id: str | None

    def __post_init__(self) -> None:
        _text(self.namespace, "mutation_epoch_namespace_invalid")
        _text(self.backend_id, "mutation_epoch_backend_invalid")
        _text(self.epoch, "mutation_epoch_identity_invalid")
        _counter(self.counter)
        if self.counter == 0:
            if self.transaction_id is not None:
                raise _fail("zero_epoch_has_transaction")
        else:
            _text(self.transaction_id, "mutation_epoch_transaction_required")

    @property
    def canonical(self) -> str:
        body = {
            "backend_id": self.backend_id,
            "counter": self.counter,
            "epoch": self.epoch,
            "namespace": self.namespace,
            "transaction_id": self.transaction_id,
        }
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"me1:{self.namespace}:{self.epoch}:{self.counter}:{digest}"


class StateMutationEpoch:
    """Atomic in-process observer advanced once per successful commit."""

    def __init__(self, *, namespace: str, backend_id: str, epoch: str) -> None:
        self.namespace = _text(namespace, "mutation_epoch_namespace_invalid")
        self.backend_id = _text(backend_id, "mutation_epoch_backend_invalid")
        self.epoch = _text(epoch, "mutation_epoch_identity_invalid")
        self._counter = 0
        self._last_transaction_id: str | None = None
        self._transactions: set[str] = set()
        self._lock = Lock()

    def snapshot(self) -> MutationEpochToken:
        with self._lock:
            return MutationEpochToken(
                namespace=self.namespace,
                backend_id=self.backend_id,
                epoch=self.epoch,
                counter=self._counter,
                transaction_id=self._last_transaction_id,
            )

    def record_commit(self, *, transaction_id: str) -> MutationEpochToken:
        """Advance after, never before, one observed successful commit."""

        selected = _text(transaction_id, "mutation_epoch_transaction_required")
        with self._lock:
            if selected in self._transactions:
                raise _fail("duplicate_committed_transaction")
            self._transactions.add(selected)
            self._counter += 1
            self._last_transaction_id = selected
            return MutationEpochToken(
                namespace=self.namespace,
                backend_id=self.backend_id,
                epoch=self.epoch,
                counter=self._counter,
                transaction_id=selected,
            )


__all__ = [
    "MutationEpochError",
    "MutationEpochToken",
    "StateMutationEpoch",
]
