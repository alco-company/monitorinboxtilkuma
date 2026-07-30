import pytest

from monitorinbox2kuma.config import load_settings


def test_load_settings_lists_all_missing_required_variables(monkeypatch) -> None:
    for name in [
        "M365_TENANT_ID",
        "M365_CLIENT_ID",
        "M365_CLIENT_SECRET",
        "M365_MAILBOX",
        "KUMA_PUSH_URL",
    ]:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError) as exc_info:
        load_settings()

    message = str(exc_info.value)
    assert "Missing required environment variables:" in message
    assert "M365_TENANT_ID" in message
    assert "M365_CLIENT_ID" in message
    assert "M365_CLIENT_SECRET" in message
    assert "M365_MAILBOX" in message
    assert ".env.example" in message


def test_load_settings_parses_multiple_allowed_senders_from_csv(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("M365_TENANT_ID", "tenant")
    monkeypatch.setenv("M365_CLIENT_ID", "client")
    monkeypatch.setenv("M365_CLIENT_SECRET", "secret")
    monkeypatch.setenv("M365_MAILBOX", "monitor@al.dk")
    monkeypatch.setenv("KUMA_PUSH_URL", "https://kuma.example.com/api/push/token")
    monkeypatch.setenv("M365_ALLOWED_SENDERS", " Monitor@PBox.dk, alerts@synology.local ,third@example.com ")
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))

    settings = load_settings()

    assert settings.allowed_senders == [
        "monitor@pbox.dk",
        "alerts@synology.local",
        "third@example.com",
    ]


def test_load_settings_uses_default_processed_folder_name(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("M365_TENANT_ID", "tenant")
    monkeypatch.setenv("M365_CLIENT_ID", "client")
    monkeypatch.setenv("M365_CLIENT_SECRET", "secret")
    monkeypatch.setenv("M365_MAILBOX", "monitor@al.dk")
    monkeypatch.setenv("KUMA_PUSH_URL", "https://kuma.example.com/api/push/token")
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.delenv("M365_PROCESSED_FOLDER", raising=False)

    settings = load_settings()

    assert settings.processed_folder_name == "Behandlet af Monitor til Kume"
