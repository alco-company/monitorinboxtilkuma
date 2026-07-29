from pathlib import Path

import pytest
import socketio

from monitorinbox2kuma.config import Settings
from monitorinbox2kuma.kuma import (
    KumaProvisioner,
    build_push_token,
    build_kuma_push_url,
    build_push_monitor_payload,
    normalize_kuma_base_url,
    render_monitor_name,
)


def make_settings() -> Settings:
    return Settings(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        mailbox="monitor@al.dk",
        mail_folder="inbox",
        allowed_senders=[],
        success_patterns=[r"success"],
        failure_patterns=[r"failed"],
        kuma_push_url=None,
        kuma_base_url="https://kuma.alco.company/dashboard",
        kuma_jwt_token="eyJ.example",
        kuma_username=None,
        kuma_password=None,
        kuma_mfa_token=None,
        kuma_auto_create_monitor=True,
        kuma_monitor_name_template="Synology Backup - {job_name}",
        kuma_monitor_description_template="Managed by monitorinbox2kuma for {job_name}",
        kuma_monitor_tags=["Backup"],
        kuma_monitor_interval_seconds=93600,
        kuma_monitor_retry_interval_seconds=600,
        kuma_monitor_resend_interval_seconds=0,
        kuma_monitor_max_retries=0,
        poll_interval_seconds=300,
        bootstrap_lookback_hours=72,
        state_file=Path("./data/state.json"),
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
        once=False,
        push_pending_on_start=False,
    )


def test_normalize_kuma_base_url_strips_dashboard_path() -> None:
    assert normalize_kuma_base_url("https://kuma.alco.company/dashboard") == "https://kuma.alco.company"


def test_build_kuma_push_url_uses_normalized_base_url() -> None:
    assert (
        build_kuma_push_url("https://kuma.alco.company/dashboard", "abc123")
        == "https://kuma.alco.company/api/push/abc123"
    )


def test_build_push_monitor_payload_creates_push_monitor_defaults() -> None:
    payload = build_push_monitor_payload(
        make_settings(),
        monitor_name="Synology Backup - ABB Teksam-Default @ adslthi.alco.dk",
        description="Managed by monitorinbox2kuma for ABB Teksam-Default @ adslthi.alco.dk",
        push_token="abc123token",
    )

    assert payload["type"] == "push"
    assert payload["name"] == "Synology Backup - ABB Teksam-Default @ adslthi.alco.dk"
    assert payload["pushToken"] == "abc123token"
    assert payload["interval"] == 93600
    assert payload["retryInterval"] == 600
    assert payload["accepted_statuscodes"] == ["200-299"]
    assert payload["conditions"] == []


def test_render_monitor_name_uses_job_name_template() -> None:
    monitor_name = render_monitor_name(make_settings(), "M365 NordTHY A/S")

    assert monitor_name == "Synology Backup - M365 NordTHY A/S"


def test_render_monitor_name_keeps_short_synology_365_name() -> None:
    monitor_name = render_monitor_name(make_settings(), "Synology 365 backupopgaven ALCO 365")

    assert monitor_name == "Synology 365 backupopgaven ALCO 365"


def test_build_push_token_is_stable_for_same_monitor_name() -> None:
    settings = make_settings()
    token_a = build_push_token(settings, "Synology 365 backupopgaven ALCO 365")
    token_b = build_push_token(settings, "Synology 365 backupopgaven ALCO 365")

    assert token_a == token_b
    assert len(token_a) == 32


class TimeoutSocket:
    def call(self, event, payload, timeout):
        raise socketio.exceptions.TimeoutError()


def test_kuma_timeout_message_is_actionable() -> None:
    provisioner = KumaProvisioner(make_settings())

    with pytest.raises(RuntimeError) as exc_info:
        provisioner._call_with_timeout(TimeoutSocket(), "loginByToken", "token")  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert "loginByToken" in message
    assert "KUMA_JWT_TOKEN" in message
    assert "KUMA_USERNAME" in message


class FakeSocket:
    def __init__(self):
        self.handlers = {}

    def on(self, event):
        def decorator(func):
            self.handlers[event] = func
            return func
        return decorator

    def connect(self, *args, **kwargs):
        monitor_list_handler = self.handlers["monitorList"]
        monitor_list_handler({"7": {"id": 7, "name": "Synology 365 backupopgaven ALCO 365", "type": "push"}})

    def call(self, event, payload, timeout):
        if event == "loginByToken":
            return {"ok": True}
        if event == "getMonitor":
            return {"ok": True, "monitor": {"id": 7, "type": "push"}}
        raise AssertionError(f"Unexpected event: {event}")

    def disconnect(self):
        return None


class FakeCreateSocket:
    def __init__(self):
        self.handlers = {}
        self.calls = []

    def on(self, event):
        def decorator(func):
            self.handlers[event] = func
            return func
        return decorator

    def connect(self, *args, **kwargs):
        monitor_list_handler = self.handlers["monitorList"]
        monitor_list_handler({})

    def call(self, event, payload=None, timeout=None):
        self.calls.append((event, payload))
        if event == "loginByToken":
            return {"ok": True}
        if event == "add":
            return {"ok": True, "monitorID": 11}
        if event == "getTags":
            return {"ok": True, "tags": [{"id": 4, "name": "Backup", "color": "#008000"}]}
        if event == "addMonitorTag":
            return {"ok": True}
        raise AssertionError(f"Unexpected event: {event}")

    def disconnect(self):
        return None


def test_existing_monitor_falls_back_to_deterministic_push_token_when_missing_from_kuma_response() -> None:
    settings = make_settings()
    provisioner = KumaProvisioner(settings, socket_factory=lambda **kwargs: FakeSocket())

    push_url = provisioner.ensure_push_monitor(
        monitor_name="Synology 365 backupopgaven ALCO 365",
        description=None,
    )

    assert push_url == build_kuma_push_url(
        settings.kuma_base_url,
        build_push_token(settings, "Synology 365 backupopgaven ALCO 365"),
    )


def test_new_monitor_gets_backup_tag_attached() -> None:
    settings = make_settings()
    fake_socket = FakeCreateSocket()
    provisioner = KumaProvisioner(settings, socket_factory=lambda **kwargs: fake_socket)

    push_url = provisioner.ensure_push_monitor(
        monitor_name="Synology 365 backupopgaven ALCO 365",
        description=None,
    )

    assert push_url == build_kuma_push_url(
        settings.kuma_base_url,
        build_push_token(settings, "Synology 365 backupopgaven ALCO 365"),
    )
    assert ("getTags", None) in fake_socket.calls
    assert ("addMonitorTag", (4, 11, "")) in fake_socket.calls
