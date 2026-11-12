"""Structured evidence: exactly what a tool observed, never what a model claimed.

An evidence record is small, immutable and reproducible: `kind` names the check, `target` names
the object, `expected` and `observed` are the two strings that can be compared by a human, and
`source` names the tool or command that produced it. The ledger deduplicates identical
observations, so a resumed task never inflates its proof by re-running the same check.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Evidence:
    kind: str
    target: str
    ok: bool
    expected: str = ""
    observed: str = ""
    source: str = ""
    at: str = field(default_factory=utcnow)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "ok": bool(self.ok),
            "expected": self.expected[:500],
            "observed": self.observed[:500],
            "source": self.source,
            "at": self.at,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Evidence":
        return cls(
            kind=str(payload.get("kind") or ""),
            target=str(payload.get("target") or ""),
            ok=bool(payload.get("ok")),
            expected=str(payload.get("expected") or ""),
            observed=str(payload.get("observed") or ""),
            source=str(payload.get("source") or ""),
            at=str(payload.get("at") or ""),
            detail=dict(payload.get("detail") or {}),
        )

    @property
    def digest(self) -> str:
        material = f"{self.kind}\x00{self.target}\x00{self.expected}\x00{self.observed}"
        return hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()


class EvidenceLedger:
    """An ordered set of evidence records, deduplicated by (kind, target, observed)."""

    def __init__(self, items: Iterable[Evidence] | None = None):
        self._items: list[Evidence] = []
        self._keys: set[str] = set()
        for item in items or ():
            self.add(item)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def add(self, item: Evidence) -> Evidence:
        if item.digest in self._keys:
            return item
        self._keys.add(item.digest)
        self._items.append(item)
        return item

    def record(
        self,
        kind: str,
        target: str,
        ok: bool,
        *,
        expected: str = "",
        observed: str = "",
        source: str = "",
        detail: dict[str, Any] | None = None,
    ) -> Evidence:
        return self.add(
            Evidence(
                kind=kind,
                target=str(target),
                ok=bool(ok),
                expected=str(expected),
                observed=str(observed),
                source=str(source),
                detail=dict(detail or {}),
            )
        )

    def extend(self, items: Iterable[Evidence]) -> None:
        for item in items:
            self.add(item)

    @property
    def ok(self) -> bool:
        return bool(self._items) and all(item.ok for item in self._items)

    def by_kind(self, *kinds: str) -> list[Evidence]:
        wanted = set(kinds)
        return [item for item in self._items if item.kind in wanted]

    def failed(self) -> list[Evidence]:
        return [item for item in self._items if not item.ok]

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, dict[str, int]] = {}
        for item in self._items:
            bucket = kinds.setdefault(item.kind, {"ok": 0, "failed": 0})
            bucket["ok" if item.ok else "failed"] += 1
        return {
            "total": len(self._items),
            "ok": sum(1 for item in self._items if item.ok),
            "failed": sum(1 for item in self._items if not item.ok),
            "kinds": kinds,
        }

    def to_list(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._items]

    @classmethod
    def from_list(cls, payload: Iterable[dict[str, Any]] | None) -> "EvidenceLedger":
        return cls(Evidence.from_dict(item) for item in payload or ())

    def to_json(self) -> str:
        return json.dumps(self.to_list(), ensure_ascii=False)


def merge_into_task(row, ledger: EvidenceLedger) -> dict[str, Any]:
    """Persist the ledger on a `LocalTask` without dropping what is already there.

    The task row is the durable store (it already syncs to SQLite), so no new table is needed and
    a restored task keeps the evidence it had. The result is also returned for the caller.
    """
    verification = dict(getattr(row, "verification", None) or {})
    existing = EvidenceLedger.from_list(verification.get("evidence") or [])
    existing.extend(ledger)
    verification["evidence"] = existing.to_list()
    verification["evidence_summary"] = existing.summary()
    verification["evidence_ok"] = existing.ok if len(existing) else False
    row.verification = verification
    return verification
