from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

DEFAULT_SUCCESS_PATTERNS = [
    r"completed successfully",
    r"\ber fuldført\b",
    r"completed backup of",
    r"backup (job|task).*(completed|finished).*(success|successful)",
    r"backup succeeded",
    r"successfully backed up",
    r"status[:\s]+success",
]

DEFAULT_FAILURE_PATTERNS = [
    r"delvist gennemført",
    r"partially completed",
    r"blev ignoreret",
    r"missede deres planlagte backupopgaver",
    r"\bfailed\b",
    r"\berror\b",
    r"\bmislykkedes\b",
    r"\baborted\b",
    r"\binterrupted\b",
    r"unsuccessful",
    r"did not complete",
]


def _require_env_values(*names: str) -> dict[str, str]:
    values: dict[str, str] = {}
    missing: list[str] = []

    for name in names:
        value = os.getenv(name, "").strip()
        if not value:
            missing.append(name)
            continue
        values[name] = value

    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(
            "Missing required environment variables: "
            f"{missing_list}. Copy .env.example to .env and fill in the values."
        )

    return values


def _optional_env(name: str) -> Optional[str]:
    value = os.getenv(name, "").strip()
    return value or None


def _parse_csv_env(name: str) -> List[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return []
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _parse_patterns(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default

    if raw.startswith("["):
        values = json.loads(raw)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError(f"{name} must be a JSON string array when using JSON syntax.")
        return values

    return [item.strip() for item in raw.split("||") if item.strip()]


def _parse_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    return normalized in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    client_id: str
    client_secret: str
    mailbox: str
    mail_folder: str
    processed_folder_name: str
    allowed_senders: List[str]
    success_patterns: List[str]
    failure_patterns: List[str]
    kuma_push_url: Optional[str]
    kuma_base_url: Optional[str]
    kuma_jwt_token: Optional[str]
    kuma_username: Optional[str]
    kuma_password: Optional[str]
    kuma_mfa_token: Optional[str]
    kuma_auto_create_monitor: bool
    kuma_monitor_name_template: str
    kuma_monitor_description_template: Optional[str]
    kuma_monitor_tags: List[str]
    kuma_monitor_interval_seconds: int
    kuma_monitor_retry_interval_seconds: int
    kuma_monitor_resend_interval_seconds: int
    kuma_monitor_max_retries: int
    poll_interval_seconds: int
    bootstrap_lookback_hours: int
    state_file: Path
    graph_timeout_seconds: int
    kuma_timeout_seconds: int
    max_messages: int
    log_level: str
    monitor_enabled: bool
    monitor_host: str
    monitor_port: int
    monitor_username: Optional[str]
    monitor_password: Optional[str]
    monitor_title: str
    once: bool
    push_pending_on_start: bool


def load_settings(*, once: bool = False) -> Settings:
    load_dotenv()

    required = _require_env_values(
        "M365_TENANT_ID",
        "M365_CLIENT_ID",
        "M365_CLIENT_SECRET",
        "M365_MAILBOX",
    )

    state_file = Path(os.getenv("STATE_FILE", "./data/state.json")).expanduser()
    poll_interval_seconds = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
    bootstrap_lookback_hours = int(os.getenv("BOOTSTRAP_LOOKBACK_HOURS", "72"))
    graph_timeout_seconds = int(os.getenv("GRAPH_TIMEOUT_SECONDS", "30"))
    kuma_timeout_seconds = int(os.getenv("KUMA_TIMEOUT_SECONDS", "15"))
    max_messages = int(os.getenv("MAX_MESSAGES", "50"))
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    monitor_enabled = _parse_bool("MONITOR_ENABLED", False)
    monitor_host = os.getenv("MONITOR_HOST", "0.0.0.0").strip() or "0.0.0.0"
    monitor_port = int(os.getenv("MONITOR_PORT", "8080"))
    monitor_username = _optional_env("MONITOR_USERNAME")
    monitor_password = _optional_env("MONITOR_PASSWORD")
    monitor_title = os.getenv("MONITOR_TITLE", "Monitor Inbox 2 Kuma").strip() or "Monitor Inbox 2 Kuma"
    push_pending_on_start = _parse_bool("PUSH_PENDING_ON_START", False)
    kuma_auto_create_monitor = _parse_bool("KUMA_AUTO_CREATE_MONITOR", False)
    mailbox = required["M365_MAILBOX"].lower()
    kuma_monitor_name_template = os.getenv(
        "KUMA_MONITOR_NAME_TEMPLATE",
        os.getenv("KUMA_MONITOR_NAME", "Synology Backup - {job_name}"),
    ).strip()
    kuma_monitor_tags = [
        item.strip()
        for item in os.getenv("KUMA_MONITOR_TAGS", "Backup").split(",")
        if item.strip()
    ]
    kuma_monitor_interval_seconds = int(os.getenv("KUMA_MONITOR_INTERVAL_SECONDS", "93600"))
    kuma_monitor_retry_interval_seconds = int(os.getenv("KUMA_MONITOR_RETRY_INTERVAL_SECONDS", "600"))
    kuma_monitor_resend_interval_seconds = int(os.getenv("KUMA_MONITOR_RESEND_INTERVAL_SECONDS", "0"))
    kuma_monitor_max_retries = int(os.getenv("KUMA_MONITOR_MAX_RETRIES", "0"))

    if poll_interval_seconds < 30:
        raise ValueError("POLL_INTERVAL_SECONDS must be at least 30 seconds.")
    if bootstrap_lookback_hours < 1:
        raise ValueError("BOOTSTRAP_LOOKBACK_HOURS must be at least 1.")
    if max_messages < 1:
        raise ValueError("MAX_MESSAGES must be at least 1.")
    if monitor_port < 1 or monitor_port > 65535:
        raise ValueError("MONITOR_PORT must be between 1 and 65535.")
    if kuma_monitor_interval_seconds < 30:
        raise ValueError("KUMA_MONITOR_INTERVAL_SECONDS must be at least 30 seconds.")
    if kuma_monitor_retry_interval_seconds < 30:
        raise ValueError("KUMA_MONITOR_RETRY_INTERVAL_SECONDS must be at least 30 seconds.")
    if kuma_monitor_resend_interval_seconds < 0:
        raise ValueError("KUMA_MONITOR_RESEND_INTERVAL_SECONDS cannot be negative.")
    if kuma_monitor_max_retries < 0:
        raise ValueError("KUMA_MONITOR_MAX_RETRIES cannot be negative.")
    if not kuma_monitor_name_template:
        raise ValueError("KUMA_MONITOR_NAME_TEMPLATE cannot be empty.")

    kuma_push_url = _optional_env("KUMA_PUSH_URL")
    kuma_base_url = _optional_env("KUMA_BASE_URL")
    kuma_jwt_token = _optional_env("KUMA_JWT_TOKEN")
    kuma_username = _optional_env("KUMA_USERNAME")
    kuma_password = _optional_env("KUMA_PASSWORD")

    if kuma_auto_create_monitor and not kuma_base_url:
        raise ValueError("KUMA_AUTO_CREATE_MONITOR requires KUMA_BASE_URL.")
    if kuma_auto_create_monitor and not (kuma_jwt_token or (kuma_username and kuma_password)):
        raise ValueError(
            "KUMA_AUTO_CREATE_MONITOR requires either KUMA_JWT_TOKEN or KUMA_USERNAME and KUMA_PASSWORD."
        )
    if not kuma_push_url and not kuma_auto_create_monitor:
        raise ValueError("Set KUMA_PUSH_URL or enable KUMA_AUTO_CREATE_MONITOR.")
    if monitor_enabled and not (monitor_username and monitor_password):
        raise ValueError("MONITOR_ENABLED requires MONITOR_USERNAME and MONITOR_PASSWORD.")

    return Settings(
        tenant_id=required["M365_TENANT_ID"],
        client_id=required["M365_CLIENT_ID"],
        client_secret=required["M365_CLIENT_SECRET"],
        mailbox=mailbox,
        mail_folder=os.getenv("M365_MAIL_FOLDER", "inbox").strip() or "inbox",
        processed_folder_name=(
            os.getenv("M365_PROCESSED_FOLDER", "Behandlet af Monitor til Kume").strip()
            or "Behandlet af Monitor til Kume"
        ),
        allowed_senders=_parse_csv_env("M365_ALLOWED_SENDERS"),
        success_patterns=_parse_patterns("SUCCESS_PATTERNS", DEFAULT_SUCCESS_PATTERNS),
        failure_patterns=_parse_patterns("FAILURE_PATTERNS", DEFAULT_FAILURE_PATTERNS),
        kuma_push_url=kuma_push_url,
        kuma_base_url=kuma_base_url,
        kuma_jwt_token=kuma_jwt_token,
        kuma_username=kuma_username,
        kuma_password=kuma_password,
        kuma_mfa_token=_optional_env("KUMA_MFA_TOKEN"),
        kuma_auto_create_monitor=kuma_auto_create_monitor,
        kuma_monitor_name_template=kuma_monitor_name_template,
        kuma_monitor_description_template=_optional_env("KUMA_MONITOR_DESCRIPTION_TEMPLATE")
        or _optional_env("KUMA_MONITOR_DESCRIPTION"),
        kuma_monitor_tags=kuma_monitor_tags,
        kuma_monitor_interval_seconds=kuma_monitor_interval_seconds,
        kuma_monitor_retry_interval_seconds=kuma_monitor_retry_interval_seconds,
        kuma_monitor_resend_interval_seconds=kuma_monitor_resend_interval_seconds,
        kuma_monitor_max_retries=kuma_monitor_max_retries,
        poll_interval_seconds=poll_interval_seconds,
        bootstrap_lookback_hours=bootstrap_lookback_hours,
        state_file=state_file,
        graph_timeout_seconds=graph_timeout_seconds,
        kuma_timeout_seconds=kuma_timeout_seconds,
        max_messages=max_messages,
        log_level=log_level,
        monitor_enabled=monitor_enabled,
        monitor_host=monitor_host,
        monitor_port=monitor_port,
        monitor_username=monitor_username,
        monitor_password=monitor_password,
        monitor_title=monitor_title,
        once=once,
        push_pending_on_start=push_pending_on_start,
    )
