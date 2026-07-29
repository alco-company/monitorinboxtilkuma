from datetime import datetime, timezone

from monitorinbox2kuma.state import State


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
