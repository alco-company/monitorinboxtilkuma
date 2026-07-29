from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from .config import Settings
from .monitor import MonitorServer, RuntimeStatus
from .graph_client import GraphClient
from .kuma import KumaClient
from .models import MailMessage, ParsedBackupStatus
from .parser import parse_backup_status
from .state import State

LOGGER = logging.getLogger(__name__)


def _matches_sender(message: MailMessage, allowed_senders: Iterable[str]) -> bool:
    allowed = list(allowed_senders)
    if not allowed:
        return True
    return message.sender.lower() in allowed


class BackupMonitorService:
    def __init__(
        self,
        settings: Settings,
        *,
        graph_client: Optional[GraphClient] = None,
        kuma_client: Optional[KumaClient] = None,
    ) -> None:
        self._settings = settings
        self._graph = graph_client or GraphClient(settings)
        self._kuma = kuma_client or KumaClient(settings)
        self._runtime_status = RuntimeStatus()
        self._monitor_server = MonitorServer(settings, self._runtime_status)

    def run(self) -> None:
        self._monitor_server.start()
        if self._settings.push_pending_on_start:
            if self._settings.kuma_auto_create_monitor:
                LOGGER.info("Skipping startup pending push because monitors are created per backup job.")
            else:
                self._kuma.push_status(status="pending", message="Service started and waiting for backup emails.")

        while True:
            try:
                self.run_once()
            except Exception as exc:
                self._runtime_status.record_error(exc)
                self._monitor_server.stop()
                raise

            if self._settings.once:
                self._runtime_status.mark_phase("idle", "Servicen har kørt én polling og er afsluttet.")
                self._monitor_server.stop()
                return

            self._runtime_status.mark_phase(
                "sleeping",
                f"Venter {self._settings.poll_interval_seconds} sekunder til næste polling.",
            )
            time.sleep(self._settings.poll_interval_seconds)

    def run_once(self) -> None:
        self._runtime_status.start_cycle()
        state = State.load(self._settings.state_file)
        messages = self._graph.fetch_messages(
            since=state.last_seen_received_at,
            limit=self._settings.max_messages,
        )

        if messages:
            newest_seen = max(message.received_at for message in messages)
            state.last_seen_received_at = newest_seen

        relevant = [message for message in messages if _matches_sender(message, self._settings.allowed_senders)]
        relevant.sort(key=lambda item: item.received_at)
        self._runtime_status.record_fetch(total_messages=len(messages), relevant_messages=len(relevant))
        processed_count = 0

        if not state.processed_message_ids and relevant:
            latest = relevant[-1]
            self._runtime_status.record_message(
                subject=latest.subject or latest.message_id,
                received_at=latest.received_at,
                activity="Indlæser seneste relevante e-mail som starttilstand.",
            )
            parsed = parse_backup_status(
                latest,
                success_patterns=self._settings.success_patterns,
                failure_patterns=self._settings.failure_patterns,
            )

            if parsed is not None:
                self._push_result(state=state, parsed=parsed)
            else:
                LOGGER.info("Bootstrap found no recognizable backup status in latest relevant email.")

            for message in relevant:
                state.remember_message(message.message_id)
                processed_count += 1
                self._delete_processed_message(message)

            state.save(self._settings.state_file)
            self._runtime_status.complete_cycle(
                processed_count=processed_count,
                activity="Bootstrap-polling er færdig.",
            )
            return

        for message in relevant:
            if message.message_id in state.processed_message_ids:
                continue

            self._runtime_status.record_message(
                subject=message.subject or message.message_id,
                received_at=message.received_at,
                activity="Behandler en ny relevant e-mail.",
            )
            parsed = parse_backup_status(
                message,
                success_patterns=self._settings.success_patterns,
                failure_patterns=self._settings.failure_patterns,
            )
            state.remember_message(message.message_id)
            processed_count += 1

            if parsed is None:
                LOGGER.info("Skipped message '%s' because no status pattern matched.", message.subject)
                self._delete_processed_message(message)
                continue

            self._push_result(state=state, parsed=parsed)
            self._delete_processed_message(message)

        if not messages:
            LOGGER.info("No new messages found in mailbox '%s'.", self._settings.mailbox)
        elif processed_count == 0:
            LOGGER.info("No unprocessed relevant messages found.")

        state.save(self._settings.state_file)
        if not messages:
            activity = "Ingen nye e-mails fundet."
        elif processed_count == 0:
            activity = "Ingen nye relevante e-mails at behandle."
        else:
            activity = f"Polling færdig. Behandlede {processed_count} e-mails."
        self._runtime_status.complete_cycle(processed_count=processed_count, activity=activity)

    def _push_result(self, *, state: State, parsed: ParsedBackupStatus) -> None:
        self._runtime_status.mark_phase(
            "polling",
            f"Sender status '{parsed.status}' til Uptime Kuma for {parsed.job_name}.",
        )
        self._kuma.push_status(status=parsed.status, message=parsed.summary, job_name=parsed.job_name)
        now = datetime.now(timezone.utc)
        state.last_pushed_status = parsed.status
        state.last_pushed_summary = parsed.summary
        state.last_pushed_at = now

    def _delete_processed_message(self, message: MailMessage) -> None:
        try:
            self._graph.delete_message(message.message_id)
        except Exception:
            LOGGER.warning(
                "Processed message '%s' could not be deleted from Inbox. "
                "Check that the app has Mail.ReadWrite and mailbox delete rights.",
                message.subject or message.message_id,
                exc_info=True,
            )
