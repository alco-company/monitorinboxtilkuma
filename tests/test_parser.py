from datetime import datetime, timezone

from monitorinbox2kuma.models import MailMessage
from monitorinbox2kuma.parser import extract_backup_job_name, parse_backup_status


def make_message(subject: str, body: str = "", sender: str = "monitor@synology.local") -> MailMessage:
    return MailMessage(
        message_id="message-1",
        internet_message_id="<message-1@example.test>",
        sender=sender,
        subject=subject,
        body=body,
        body_preview=body[:100],
        received_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )


def test_parser_detects_success_from_subject() -> None:
    parsed = parse_backup_status(
        make_message("Hyper Backup task completed successfully"),
        success_patterns=[r"completed successfully"],
        failure_patterns=[r"\bfailed\b"],
    )

    assert parsed is not None
    assert parsed.job_name == "Hyper Backup task completed successfully"
    assert parsed.status == "up"


def test_parser_detects_failure_from_body() -> None:
    parsed = parse_backup_status(
        make_message(
            "Synology notification",
            body="The backup job failed because the destination disk is full.",
        ),
        success_patterns=[r"completed successfully"],
        failure_patterns=[r"\bfailed\b", r"disk is full"],
    )

    assert parsed is not None
    assert parsed.job_name == "Synology notification"
    assert parsed.status == "down"


def test_parser_prefers_failure_when_both_match() -> None:
    parsed = parse_backup_status(
        make_message(
            "Backup report",
            body="Task completed successfully after retries, but one phase failed.",
        ),
        success_patterns=[r"completed successfully"],
        failure_patterns=[r"\bfailed\b"],
    )

    assert parsed is not None
    assert parsed.job_name == "Backup report"
    assert parsed.status == "down"


def test_parser_returns_none_when_no_pattern_matches() -> None:
    parsed = parse_backup_status(
        make_message("Weekly report", body="Everything is normal."),
        success_patterns=[r"completed successfully"],
        failure_patterns=[r"\bfailed\b"],
    )

    assert parsed is None


def test_extracts_job_name_for_active_backup_for_business_subject() -> None:
    job_name = extract_backup_job_name(
        make_message(
            "adslthi.alco.dk Active Backup for Business - backupopgave "
            "Teksam-Default på ALCOStore er fuldført"
        )
    )

    assert job_name == "ABB Teksam-Default @ adslthi.alco.dk"


def test_extracts_job_name_for_completed_backup_subject() -> None:
    job_name = extract_backup_job_name(
        make_message(
            "Completed backup of Virtualmin on hosting-2.alco.company to "
            "/Hetzner/hosting-2/20260729-incr on FTP server adslthi.alco.dk"
        )
    )

    assert job_name == "Backup Virtualmin @ hosting-2.alco.company"


def test_extracts_job_name_for_active_backup_m365_subject() -> None:
    job_name = extract_backup_job_name(
        make_message(
            "Active Backup for Microsoft 365 - backupopgaven [NordTHY A/S] "
            "på [ALCOStore] er delvist gennemført"
        )
    )

    assert job_name == "M365 NordTHY A/S"


def test_parser_treats_partially_completed_as_failure() -> None:
    parsed = parse_backup_status(
        make_message(
            "Active Backup for Microsoft 365 - backupopgaven [Danavinduer] "
            "på [ALCOStore] er delvist gennemført"
        ),
        success_patterns=[r"\ber fuldført\b", r"completed backup of"],
        failure_patterns=[r"delvist gennemført", r"\bfailed\b"],
    )

    assert parsed is not None
    assert parsed.job_name == "M365 Danavinduer"
    assert parsed.status == "down"
