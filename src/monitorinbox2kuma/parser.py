from __future__ import annotations

import re
from typing import Iterable, Optional

from .models import MailMessage, ParsedBackupStatus

ABB_SUBJECT_RE = re.compile(
    r"^(?P<host>\S+)\s+Active Backup for Business - backupopgave\s+"
    r"(?P<job>.+?)\s+på\s+(?P<store>.+?)\s+er\s+(?P<result>.+)$",
    flags=re.IGNORECASE,
)
M365_SUBJECT_RE = re.compile(
    r"^Active Backup for Microsoft 365 - backupopgaven\s+\[(?P<job>.+?)\]\s+"
    r"på\s+\[(?P<store>.+?)\]\s+er\s+(?P<result>.+)$",
    flags=re.IGNORECASE,
)
COMPLETED_BACKUP_RE = re.compile(
    r"^Completed backup of\s+(?P<job>.+?)\s+on\s+(?P<host>\S+)\s+to\s+.+$",
    flags=re.IGNORECASE,
)


def _normalize_text(parts: Iterable[str]) -> str:
    return "\n".join(part.strip() for part in parts if part).strip().lower()


def extract_backup_job_name(message: MailMessage) -> str:
    subject = message.subject.strip()

    match = ABB_SUBJECT_RE.match(subject)
    if match:
        job = match.group("job").strip()
        host = match.group("host").strip()
        return f"ABB {job} @ {host}"

    match = M365_SUBJECT_RE.match(subject)
    if match:
        job = match.group("job").strip()
        return f"M365 {job}"

    match = COMPLETED_BACKUP_RE.match(subject)
    if match:
        job = match.group("job").strip()
        host = match.group("host").strip()
        return f"Backup {job} @ {host}"

    return subject[:160] or message.sender


def parse_backup_status(
    message: MailMessage,
    *,
    success_patterns: Iterable[str],
    failure_patterns: Iterable[str],
) -> Optional[ParsedBackupStatus]:
    content = _normalize_text([message.subject, message.body_preview, message.body])
    job_name = extract_backup_job_name(message)

    for pattern in failure_patterns:
        if re.search(pattern, content, flags=re.IGNORECASE):
            return ParsedBackupStatus(
                job_name=job_name,
                status="down",
                summary=f"Backup failure detected for {job_name}.",
            )

    for pattern in success_patterns:
        if re.search(pattern, content, flags=re.IGNORECASE):
            return ParsedBackupStatus(
                job_name=job_name,
                status="up",
                summary=f"Backup success detected for {job_name}.",
            )

    return None
