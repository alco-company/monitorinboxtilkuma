from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import List, Optional

from .models import MailMessage


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


@dataclass
class ProcessedMailRecord:
    sender: str
    subject: str
    received_at: Optional[datetime]
    processed_at: datetime


@dataclass
class State:
    processed_message_ids: List[str] = field(default_factory=list)
    last_seen_received_at: Optional[datetime] = None
    last_pushed_status: Optional[str] = None
    last_pushed_summary: Optional[str] = None
    last_pushed_at: Optional[datetime] = None
    recent_processed_messages: List[ProcessedMailRecord] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls()

        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            processed_message_ids=payload.get("processed_message_ids", []),
            last_seen_received_at=_parse_datetime(payload.get("last_seen_received_at")),
            last_pushed_status=payload.get("last_pushed_status"),
            last_pushed_summary=payload.get("last_pushed_summary"),
            last_pushed_at=_parse_datetime(payload.get("last_pushed_at")),
            recent_processed_messages=[
                ProcessedMailRecord(
                    sender=item.get("sender", ""),
                    subject=item.get("subject", ""),
                    received_at=_parse_datetime(item.get("received_at")),
                    processed_at=_parse_datetime(item.get("processed_at")) or datetime.min,
                )
                for item in payload.get("recent_processed_messages", [])
                if isinstance(item, dict)
            ],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "processed_message_ids": self.processed_message_ids[-250:],
            "last_seen_received_at": _format_datetime(self.last_seen_received_at),
            "last_pushed_status": self.last_pushed_status,
            "last_pushed_summary": self.last_pushed_summary,
            "last_pushed_at": _format_datetime(self.last_pushed_at),
            "recent_processed_messages": [
                {
                    **asdict(item),
                    "received_at": _format_datetime(item.received_at),
                    "processed_at": _format_datetime(item.processed_at),
                }
                for item in self.recent_processed_messages[-50:]
            ],
        }

        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            temp_path = Path(handle.name)

        temp_path.replace(path)

    def remember_message(self, message_id: str) -> None:
        self.processed_message_ids.append(message_id)
        self.processed_message_ids = self.processed_message_ids[-250:]

    def record_processed_message(self, message: MailMessage, *, processed_at: datetime) -> None:
        self.recent_processed_messages.append(
            ProcessedMailRecord(
                sender=message.sender,
                subject=message.subject,
                received_at=message.received_at,
                processed_at=processed_at,
            )
        )
        self.recent_processed_messages = self.recent_processed_messages[-50:]
