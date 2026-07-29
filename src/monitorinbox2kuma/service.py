from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

from .config import Settings
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

    def run(self) -> None:
        if self._settings.push_pending_on_start:
            if self._settings.kuma_auto_create_monitor:
                LOGGER.info("Skipping startup pending push because monitors are created per backup job.")
            else:
                self._kuma.push_status(status="pending", message="Service started and waiting for backup emails.")

        while True:
            self.run_once()
            if self._settings.once:
                return
            time.sleep(self._settings.poll_interval_seconds)

    def run_once(self) -> None:
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

        if not state.processed_message_ids and relevant:
            latest = relevant[-1]
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
                self._delete_processed_message(message)

            state.save(self._settings.state_file)
            return

        processed_any = False
        for message in relevant:
            if message.message_id in state.processed_message_ids:
                continue

            parsed = parse_backup_status(
                message,
                success_patterns=self._settings.success_patterns,
                failure_patterns=self._settings.failure_patterns,
            )
            state.remember_message(message.message_id)

            if parsed is None:
                LOGGER.info("Skipped message '%s' because no status pattern matched.", message.subject)
                self._delete_processed_message(message)
                processed_any = True
                continue

            self._push_result(state=state, parsed=parsed)
            self._delete_processed_message(message)
            processed_any = True

        if not messages:
            LOGGER.info("No new messages found in mailbox '%s'.", self._settings.mailbox)
        elif not processed_any:
            LOGGER.info("No unprocessed relevant messages found.")

        state.save(self._settings.state_file)

    def _push_result(self, *, state: State, parsed: ParsedBackupStatus) -> None:
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
