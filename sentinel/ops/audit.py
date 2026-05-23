"""
Append-only audit log with hash chaining.

Used for deployment evidence and post-incident review. Each event includes the
previous event hash, so local tampering is detectable by replaying the chain.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sentinel.core.types import utc_now

DEFAULT_AUDIT_LOG_PATH = Path("logs") / "sentinel_audit.jsonl"


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    timestamp: str
    actor: str
    event_type: str
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str


@dataclass(frozen=True)
class AuditVerificationResult:
    valid: bool
    event_count: int
    first_error: str = ""


class AppendOnlyAuditLog:
    """JSONL append-only audit log with deterministic event hashes."""

    def __init__(self, path: Path | str = DEFAULT_AUDIT_LOG_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        actor: str = "system",
    ) -> AuditEvent:
        previous_hash = self._last_hash()
        timestamp = utc_now().isoformat()
        event_id = hashlib.sha256(
            f"{timestamp}|{actor}|{event_type}|{previous_hash}".encode("utf-8")
        ).hexdigest()[:16]
        event_hash = _event_hash(
            event_id=event_id,
            timestamp=timestamp,
            actor=actor,
            event_type=event_type,
            payload=payload,
            previous_hash=previous_hash,
        )
        event = AuditEvent(
            event_id=event_id,
            timestamp=timestamp,
            actor=actor,
            event_type=event_type,
            payload=payload,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(event), sort_keys=True) + "\n")
        return event

    def read_all(self) -> list[AuditEvent]:
        if not self.path.exists():
            return []
        events: list[AuditEvent] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                data = json.loads(line)
                events.append(AuditEvent(**data))
        return events

    def verify(self) -> AuditVerificationResult:
        previous_hash = "GENESIS"
        count = 0
        for event in self.read_all():
            expected = _event_hash(
                event_id=event.event_id,
                timestamp=event.timestamp,
                actor=event.actor,
                event_type=event.event_type,
                payload=event.payload,
                previous_hash=event.previous_hash,
            )
            if event.previous_hash != previous_hash:
                return AuditVerificationResult(False, count, "previous_hash mismatch")
            if event.event_hash != expected:
                return AuditVerificationResult(False, count, "event_hash mismatch")
            previous_hash = event.event_hash
            count += 1
        return AuditVerificationResult(True, count)

    def _last_hash(self) -> str:
        events = self.read_all()
        return events[-1].event_hash if events else "GENESIS"


def _event_hash(
    event_id: str,
    timestamp: str,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    previous_hash: str,
) -> str:
    canonical = json.dumps({
        "actor": actor,
        "event_id": event_id,
        "event_type": event_type,
        "payload": payload,
        "previous_hash": previous_hash,
        "timestamp": timestamp,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
