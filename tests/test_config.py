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
