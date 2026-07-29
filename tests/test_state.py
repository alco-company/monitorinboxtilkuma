from datetime import datetime, timezone

from monitorinbox2kuma.state import State
from monitorinbox2kuma.models import MailMessage


def test_state_roundtrip(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    state = State(
        processed_message_ids=["a", "b"],
        last_seen_received_at=datetime(2026, 7, 29, 10, 15, tzinfo=timezone.utc),
        last_pushed_status="up",
        last_pushed_summary="Backup OK",
        last_pushed_at=datetime(2026, 7, 29, 10, 16, tzinfo=timezone.utc),
    )

    state.save(state_path)
    loaded = State.load(state_path)

    assert loaded.processed_message_ids == ["a", "b"]
    assert loaded.last_seen_received_at == datetime(2026, 7, 29, 10, 15, tzinfo=timezone.utc)
    assert loaded.last_pushed_status == "up"
    assert loaded.last_pushed_summary == "Backup OK"
    assert loaded.last_pushed_at == datetime(2026, 7, 29, 10, 16, tzinfo=timezone.utc)


def test_state_roundtrip_with_processed_message_history(tmp_path) -> None:
    state_path = tmp_path / "state.json"
    state = State()
    state.record_processed_message(
        MailMessage(
            message_id="message-1",
            internet_message_id="<message-1@example.test>",
            sender="alerts@synology.local",
            subject="Hyper Backup task completed successfully",
            body="",
            body_preview="",
            received_at=datetime(2026, 7, 29, 10, 14, tzinfo=timezone.utc),
        ),
        processed_at=datetime(2026, 7, 29, 10, 16, tzinfo=timezone.utc),
    )

    state.save(state_path)
    loaded = State.load(state_path)

    assert len(loaded.recent_processed_messages) == 1
    assert loaded.recent_processed_messages[0].sender == "alerts@synology.local"
    assert loaded.recent_processed_messages[0].subject == "Hyper Backup task completed successfully"
