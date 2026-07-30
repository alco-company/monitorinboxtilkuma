from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import msal
import requests

from .config import Settings
from .models import MailMessage

LOGGER = logging.getLogger(__name__)


class GraphClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._app = msal.ConfidentialClientApplication(
            settings.client_id,
            authority=f"https://login.microsoftonline.com/{settings.tenant_id}",
            client_credential=settings.client_secret,
        )
        self._session = requests.Session()
        self._processed_folder_id: Optional[str] = None

    def _access_token(self) -> str:
        token_result = self._app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        access_token = token_result.get("access_token")
        if not access_token:
            raise RuntimeError(f"Unable to acquire Microsoft Graph access token: {token_result}")
        return access_token

    def move_message(self, message_id: str) -> None:
        access_token = self._access_token()
        destination_id = self._processed_destination_folder_id(access_token)
        url = f"https://graph.microsoft.com/v1.0/users/{self._settings.mailbox}/messages/{message_id}/move"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        response = self._session.post(
            url,
            json={"destinationId": destination_id},
            headers=headers,
            timeout=self._settings.graph_timeout_seconds,
        )
        response.raise_for_status()
        LOGGER.info(
            "Moved processed message '%s' to '%s' in mailbox '%s'.",
            message_id,
            self._settings.processed_folder_name,
            self._settings.mailbox,
        )

    def fetch_messages(self, *, since: Optional[datetime], limit: int) -> List[MailMessage]:
        access_token = self._access_token()
        url = (
            "https://graph.microsoft.com/v1.0/"
            f"users/{self._settings.mailbox}/mailFolders/{self._settings.mail_folder}/messages"
        )

        effective_since = since
        if effective_since is None:
            effective_since = datetime.now(timezone.utc) - timedelta(hours=self._settings.bootstrap_lookback_hours)

        received_filter = effective_since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "$top": min(limit, self._settings.max_messages),
            "$orderby": "receivedDateTime desc",
            "$filter": f"receivedDateTime ge {received_filter}",
            "$select": ",".join(
                [
                    "id",
                    "internetMessageId",
                    "subject",
                    "from",
                    "bodyPreview",
                    "body",
                    "receivedDateTime",
                ]
            ),
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Prefer": 'outlook.body-content-type="text"',
        }

        response = self._session.get(
            url,
            params=params,
            headers=headers,
            timeout=self._settings.graph_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("value", [])
        LOGGER.info("Fetched %s messages from Microsoft Graph.", len(items))

        messages: List[MailMessage] = []
        for item in items:
            sender = (
                item.get("from", {})
                .get("emailAddress", {})
                .get("address", "")
                .strip()
                .lower()
            )
            messages.append(
                MailMessage(
                    message_id=item["id"],
                    internet_message_id=item.get("internetMessageId"),
                    sender=sender,
                    subject=(item.get("subject") or "").strip(),
                    body=(item.get("body", {}) or {}).get("content", "") or "",
                    body_preview=(item.get("bodyPreview") or "").strip(),
                    received_at=datetime.fromisoformat(item["receivedDateTime"].replace("Z", "+00:00")),
                )
            )

        return messages

    def _processed_destination_folder_id(self, access_token: str) -> str:
        if self._processed_folder_id:
            return self._processed_folder_id

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        folder_name = self._settings.processed_folder_name
        base_url = (
            "https://graph.microsoft.com/v1.0/"
            f"users/{self._settings.mailbox}/mailFolders/{self._settings.mail_folder}/childFolders"
        )

        response = self._session.get(
            base_url,
            params={
                "$top": 100,
                "$select": "id,displayName",
            },
            headers=headers,
            timeout=self._settings.graph_timeout_seconds,
        )
        response.raise_for_status()

        for item in response.json().get("value", []):
            if (item.get("displayName") or "").strip().casefold() == folder_name.casefold():
                self._processed_folder_id = item["id"]
                return self._processed_folder_id

        create_response = self._session.post(
            base_url,
            json={"displayName": folder_name},
            headers={**headers, "Content-Type": "application/json"},
            timeout=self._settings.graph_timeout_seconds,
        )
        create_response.raise_for_status()
        folder_id = create_response.json()["id"]
        self._processed_folder_id = folder_id
        LOGGER.info(
            "Created processed mail folder '%s' under '%s' for mailbox '%s'.",
            folder_name,
            self._settings.mail_folder,
            self._settings.mailbox,
        )
        return folder_id
