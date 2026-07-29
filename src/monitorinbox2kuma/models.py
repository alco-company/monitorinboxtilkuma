from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class MailMessage:
    message_id: str
    internet_message_id: Optional[str]
    sender: str
    subject: str
    body: str
    body_preview: str
    received_at: datetime


@dataclass(frozen=True)
class ParsedBackupStatus:
    job_name: str
    status: str
    summary: str
