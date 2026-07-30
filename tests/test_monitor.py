import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from monitorinbox2kuma.config import Settings
from monitorinbox2kuma.monitor import MonitorServer, RuntimeStatus
from monitorinbox2kuma.state import State


def make_settings(state_path: Path) -> Settings:
    return Settings(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        mailbox="monitor@al.dk",
        mail_folder="inbox",
        processed_folder_name="Behandlet af Monitor til Kume",
        allowed_senders=[],
        success_patterns=[r"success"],
        failure_patterns=[r"failed"],
        kuma_push_url="https://kuma.example.com/api/push/token",
        kuma_base_url=None,
        kuma_jwt_token=None,
        kuma_username=None,
        kuma_password=None,
        kuma_mfa_token=None,
        kuma_auto_create_monitor=False,
        kuma_monitor_name_template="Synology Backup - {job_name}",
        kuma_monitor_description_template=None,
        kuma_monitor_tags=["Backup"],
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
        monitor_enabled=True,
        monitor_host="127.0.0.1",
        monitor_port=0,
        monitor_username="admin",
        monitor_password="secret123",
        monitor_title="Backup Monitor",
        once=False,
        push_pending_on_start=False,
    )


def auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def test_monitor_requires_basic_auth(tmp_path) -> None:
    settings = make_settings(tmp_path / "state.json")
    runtime = RuntimeStatus()
    server = MonitorServer(settings, runtime)
    server.start()

    try:
        with pytest.raises(HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{server.port}/api/status")
        assert exc_info.value.code == 401
    finally:
        server.stop()


def test_monitor_returns_status_snapshot_and_html(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    State(
        processed_message_ids=["a", "b", "c"],
        last_seen_received_at=datetime(2026, 7, 29, 10, 15, tzinfo=timezone.utc),
        last_pushed_status="up",
        last_pushed_summary="Backup OK",
        last_pushed_at=datetime(2026, 7, 29, 10, 16, tzinfo=timezone.utc),
    ).save(state_path)

    settings = make_settings(state_path)
    runtime = RuntimeStatus()
    runtime.start_cycle()
    runtime.record_fetch(total_messages=4, relevant_messages=2)
    runtime.record_message(
        subject="Hyper Backup task completed successfully",
        received_at=datetime(2026, 7, 29, 10, 14, tzinfo=timezone.utc),
        activity="Behandler en ny relevant e-mail.",
    )
    runtime.update_inbox_messages(
        [
            {
                "sender": "someone@example.com",
                "subject": "Weekly report",
                "subject_preview": "Weekly report",
                "received_at": "2026-07-29T10:13:00+00:00",
            }
        ]
    )
    runtime.complete_cycle(processed_count=2, activity="Polling færdig. Behandlede 2 e-mails.")
    runtime.schedule_next_poll(after_seconds=300, activity="Venter 300 sekunder til næste polling.")

    server = MonitorServer(settings, runtime, lambda: True)
    server.start()

    try:
        request = Request(f"http://127.0.0.1:{server.port}/api/status")
        request.add_header("Authorization", auth_header("admin", "secret123"))
        with urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))

        assert payload["title"] == "Backup Monitor"
        assert payload["service_status"] == "up"
        assert payload["display_timezone"] == "Europe/Copenhagen"
        assert payload["runtime"]["last_fetch_count"] == 4
        assert payload["runtime"]["last_processed_count"] == 2
        assert payload["runtime"]["next_poll_due_at"] is not None
        assert payload["runtime"]["next_poll_due_at_display"] is not None
        assert payload["runtime"]["inbox_messages"][0]["sender"] == "someone@example.com"
        assert payload["state"]["last_pushed_summary"] == "Backup OK"

        html_request = Request(f"http://127.0.0.1:{server.port}/")
        html_request.add_header("Authorization", auth_header("admin", "secret123"))
        with urlopen(html_request) as response:
            page = response.read().decode("utf-8")

        assert "Backup Monitor" in page
        assert "Backup OK" in page
        assert "Europe/Copenhagen" in page
        assert "Kør manuel poll nu" in page
        assert "next-poll-countdown" in page
        assert "Inbox nu" in page
        assert "Behandlede mails" in page
        assert "someone@example.com" in page
    finally:
        server.stop()


def test_monitor_manual_poll_endpoint_triggers_callback(tmp_path) -> None:
    settings = make_settings(tmp_path / "state.json")
    runtime = RuntimeStatus()
    calls = []

    def trigger() -> bool:
        calls.append("poll")
        return True

    server = MonitorServer(settings, runtime, trigger)
    server.start()

    try:
        request = Request(f"http://127.0.0.1:{server.port}/api/poll", method="POST")
        request.add_header("Authorization", auth_header("admin", "secret123"))
        with urlopen(request) as response:
            assert response.geturl().endswith("/")

        assert calls == ["poll"]
    finally:
        server.stop()
