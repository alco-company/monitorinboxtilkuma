from datetime import datetime, timezone
from pathlib import Path

from monitorinbox2kuma.config import Settings
from monitorinbox2kuma.models import MailMessage
from monitorinbox2kuma.service import BackupMonitorService


class FakeGraphClient:
    def __init__(self, messages):
        self.messages = list(messages)
        self.deleted_message_ids = []

    def fetch_messages(self, *, since, limit):
        return list(self.messages)

    def delete_message(self, message_id: str) -> None:
        self.deleted_message_ids.append(message_id)


class FakeKumaClient:
    def __init__(self):
        self.pushes = []

    def push_status(self, *, status: str, message: str, job_name=None, ping=None) -> None:
        self.pushes.append((status, message, job_name, ping))


def make_settings(state_path: Path) -> Settings:
    return Settings(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        mailbox="monitor@al.dk",
        mail_folder="inbox",
        allowed_senders=["alerts@synology.local"],
        success_patterns=[r"completed successfully"],
        failure_patterns=[r"\bfailed\b"],
        kuma_push_url="https://kuma.example.com/api/push/token",
        kuma_base_url=None,
        kuma_jwt_token=None,
        kuma_username=None,
        kuma_password=None,
        kuma_mfa_token=None,
        kuma_auto_create_monitor=False,
        kuma_monitor_name_template="Synology Backup - {job_name}",
        kuma_monitor_description_template=None,
        kuma_monitor_interval_seconds=93600,
        kuma_monitor_retry_interval_seconds=600,
        kuma_monitor_resend_interval_seconds=0,
        kuma_monitor_max_retries=0,
        poll_interval_seconds=300,
        bootstrap_lookback_hours=72,
        state_file=state_path,
        graph_timeout_seconds=30,
        kuma_timeout_seconds=15,
        max_messages=50,
        log_level="INFO",
        monitor_enabled=False,
        monitor_host="127.0.0.1",
        monitor_port=8080,
        monitor_username=None,
        monitor_password=None,
        monitor_title="Monitor Inbox 2 Kuma",
        once=True,
        push_pending_on_start=False,
    )


def make_message(
    *,
    message_id: str = "message-1",
    sender: str = "alerts@synology.local",
    subject: str = "Hyper Backup task completed successfully",
) -> MailMessage:
    return MailMessage(
        message_id=message_id,
        internet_message_id=f"<{message_id}@example.test>",
        sender=sender,
        subject=subject,
        body=subject,
        body_preview=subject,
        received_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )


def test_service_deletes_processed_relevant_messages(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    graph = FakeGraphClient([make_message()])
    kuma = FakeKumaClient()
    service = BackupMonitorService(make_settings(state_path), graph_client=graph, kuma_client=kuma)

    service.run_once()

    assert graph.deleted_message_ids == ["message-1"]
    assert kuma.pushes[0][0] == "up"
    assert kuma.pushes[0][2] == "Hyper Backup task completed successfully"
