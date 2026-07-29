from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
import socketio

from .config import Settings

LOGGER = logging.getLogger(__name__)


def normalize_kuma_base_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("KUMA_BASE_URL must be a full URL, for example https://kuma.example.com/dashboard")

    path = parsed.path.rstrip("/")
    if path.endswith("/dashboard"):
        path = path[: -len("/dashboard")]

    normalized = parsed._replace(path=path, params="", query="", fragment="")
    return urlunparse(normalized).rstrip("/")


def build_kuma_push_url(base_url: str, push_token: str) -> str:
    return f"{normalize_kuma_base_url(base_url)}/api/push/{push_token}"


def render_monitor_name(settings: Settings, job_name: str) -> str:
    return settings.kuma_monitor_name_template.format(job_name=job_name, mailbox=settings.mailbox)


def render_monitor_description(settings: Settings, job_name: str) -> Optional[str]:
    if not settings.kuma_monitor_description_template:
        return None
    return settings.kuma_monitor_description_template.format(job_name=job_name, mailbox=settings.mailbox)


def build_push_monitor_payload(settings: Settings, *, monitor_name: str, description: Optional[str]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "push",
        "name": monitor_name,
        "description": description,
        "interval": settings.kuma_monitor_interval_seconds,
        "retryInterval": settings.kuma_monitor_retry_interval_seconds,
        "resendInterval": settings.kuma_monitor_resend_interval_seconds,
        "maxretries": settings.kuma_monitor_max_retries,
        "active": True,
        "notificationIDList": {},
        "accepted_statuscodes_json": "[\"200-299\"]",
        "conditions": "[]",
    }
    return payload


class KumaProvisioner:
    def __init__(
        self,
        settings: Settings,
        *,
        socket_factory: Optional[Callable[..., socketio.Client]] = None,
    ) -> None:
        self._settings = settings
        self._socket_factory = socket_factory or socketio.Client
        self._monitor_list: Dict[str, Dict[str, Any]] = {}
        self._monitor_list_ready = threading.Event()

    def ensure_push_monitor(self, *, monitor_name: str, description: Optional[str]) -> str:
        if not self._settings.kuma_base_url:
            raise RuntimeError("Kuma auto provisioning requires a base URL.")
        if not self._settings.kuma_jwt_token and not (
            self._settings.kuma_username and self._settings.kuma_password
        ):
            raise RuntimeError("Kuma auto provisioning requires a JWT token or username/password.")

        base_url = normalize_kuma_base_url(self._settings.kuma_base_url)
        session = requests.Session()
        sio = self._socket_factory(reconnection=False, http_session=session, logger=False, engineio_logger=False)
        self._register_handlers(sio)

        try:
            try:
                sio.connect(base_url, wait_timeout=self._settings.kuma_timeout_seconds, socketio_path="socket.io")
            except socketio.exceptions.ConnectionError as exc:
                raise RuntimeError(
                    "Could not connect to Uptime Kuma over Socket.IO. "
                    "Check KUMA_BASE_URL and make sure any reverse proxy allows WebSocket traffic."
                ) from exc
            self._login(sio)
            self._monitor_list_ready.wait(timeout=self._settings.kuma_timeout_seconds)

            monitor = self._find_monitor_by_name(monitor_name)
            if monitor is None:
                monitor_id = self._create_monitor(sio, monitor_name=monitor_name, description=description)
            else:
                if monitor.get("type") != "push":
                    raise RuntimeError(
                        f"Existing monitor '{monitor_name}' is type '{monitor.get('type')}', not 'push'."
                    )
                monitor_id = int(monitor["id"])

            full_monitor = self._get_monitor(sio, monitor_id)
            push_token = full_monitor.get("pushToken")
            if not push_token:
                raise RuntimeError("Kuma monitor was found or created, but no pushToken was returned.")

            return build_kuma_push_url(base_url, str(push_token))
        finally:
            try:
                sio.disconnect()
            except Exception:
                LOGGER.debug("Ignoring Socket.IO disconnect error.", exc_info=True)

    def _register_handlers(self, sio: socketio.Client) -> None:
        @sio.on("monitorList")
        def on_monitor_list(payload: Any) -> None:
            self._monitor_list = self._normalize_monitor_list(payload)
            self._monitor_list_ready.set()

        @sio.on("updateMonitorIntoList")
        def on_monitor_update(payload: Any) -> None:
            if isinstance(payload, dict) and payload.get("id") is not None:
                self._monitor_list[str(payload["id"])] = payload
                self._monitor_list_ready.set()

        @sio.on("deleteMonitorFromList")
        def on_monitor_delete(payload: Any) -> None:
            monitor_id = None
            if isinstance(payload, dict):
                monitor_id = payload.get("id") or payload.get("monitorID") or payload.get("monitorId")
            elif payload is not None:
                monitor_id = payload
            if monitor_id is not None:
                self._monitor_list.pop(str(monitor_id), None)

    def _login(self, sio: socketio.Client) -> None:
        if self._settings.kuma_jwt_token:
            try:
                response = self._login_by_token(sio, self._settings.kuma_jwt_token)
                if response.get("ok"):
                    return
                LOGGER.warning("Kuma token login failed, falling back to password login if configured.")
            except RuntimeError:
                if not (self._settings.kuma_username and self._settings.kuma_password):
                    raise
                LOGGER.warning(
                    "Kuma token login timed out, falling back to username/password login because it is configured."
                )

        if not self._settings.kuma_username or not self._settings.kuma_password:
            raise RuntimeError("Kuma JWT login failed and no username/password fallback is configured.")

        payload: Dict[str, Any] = {
            "username": self._settings.kuma_username,
            "password": self._settings.kuma_password,
        }
        if self._settings.kuma_mfa_token:
            payload["token"] = self._settings.kuma_mfa_token

        response = sio.call("login", payload, timeout=self._settings.kuma_timeout_seconds)
        if response.get("tokenRequired") and not self._settings.kuma_mfa_token:
            raise RuntimeError("Kuma login requires a 2FA token. Set KUMA_MFA_TOKEN.")
        if not response.get("ok"):
            raise RuntimeError(f"Kuma login failed: {response}")

    def _login_by_token(self, sio: socketio.Client, jwt_token: str) -> Dict[str, Any]:
        response = self._call_with_timeout(sio, "loginByToken", jwt_token)
        if response.get("ok"):
            return response

        alt_response = self._call_with_timeout(
            sio,
            "loginByToken",
            {"jwtToken": jwt_token},
        )
        return alt_response

    def _call_with_timeout(self, sio: socketio.Client, event: str, payload: Any) -> Dict[str, Any]:
        try:
            response = sio.call(event, payload, timeout=self._settings.kuma_timeout_seconds)
        except socketio.exceptions.TimeoutError as exc:
            raise RuntimeError(
                f"Timed out while calling Uptime Kuma event '{event}'. "
                "If you use KUMA_JWT_TOKEN, it may be expired or blocked by a reverse proxy without WebSocket support. "
                "Try a direct internal KUMA_BASE_URL or configure KUMA_USERNAME and KUMA_PASSWORD as fallback."
            ) from exc
        if not isinstance(response, dict):
            raise RuntimeError(f"Uptime Kuma returned an invalid response for event '{event}': {response!r}")
        return response

    def _create_monitor(self, sio: socketio.Client, *, monitor_name: str, description: Optional[str]) -> int:
        payload = build_push_monitor_payload(self._settings, monitor_name=monitor_name, description=description)
        response = sio.call("add", payload, timeout=self._settings.kuma_timeout_seconds)
        if not response.get("ok"):
            raise RuntimeError(f"Failed to create Kuma monitor: {response}")

        monitor_id = response.get("monitorID", response.get("monitorId"))
        if monitor_id is None:
            raise RuntimeError(f"Kuma monitor creation succeeded but returned no monitor id: {response}")
        return int(monitor_id)

    def _get_monitor(self, sio: socketio.Client, monitor_id: int) -> Dict[str, Any]:
        response = sio.call("getMonitor", monitor_id, timeout=self._settings.kuma_timeout_seconds)
        if not response.get("ok"):
            raise RuntimeError(f"Failed to fetch Kuma monitor {monitor_id}: {response}")
        monitor = response.get("monitor")
        if not isinstance(monitor, dict):
            raise RuntimeError(f"Kuma returned an invalid monitor payload: {response}")
        return monitor

    def _find_monitor_by_name(self, monitor_name: str) -> Optional[Dict[str, Any]]:
        for monitor in self._monitor_list.values():
            if monitor.get("name") == monitor_name:
                return monitor
        return None

    @staticmethod
    def _normalize_monitor_list(payload: Any) -> Dict[str, Dict[str, Any]]:
        if isinstance(payload, dict):
            return {str(key): value for key, value in payload.items() if isinstance(value, dict)}
        if isinstance(payload, list):
            normalized: Dict[str, Dict[str, Any]] = {}
            for value in payload:
                if isinstance(value, dict) and value.get("id") is not None:
                    normalized[str(value["id"])] = value
            return normalized
        return {}


class KumaClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session = requests.Session()
        self._push_url = settings.kuma_push_url
        self._push_urls_by_monitor: Dict[str, str] = {}
        self._provision_lock = threading.Lock()

    def push_status(self, *, status: str, message: str, job_name: Optional[str] = None, ping: Optional[int] = None) -> None:
        parsed = urlparse(self._resolve_push_url(job_name=job_name))
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["status"] = status
        query["msg"] = message
        if ping is not None:
            query["ping"] = str(ping)

        final_url = urlunparse(parsed._replace(query=urlencode(query)))
        response = self._session.get(final_url, timeout=self._settings.kuma_timeout_seconds)
        response.raise_for_status()
        LOGGER.info("Pushed '%s' status to Uptime Kuma.", status)

    def _resolve_push_url(self, *, job_name: Optional[str]) -> str:
        with self._provision_lock:
            if self._settings.kuma_auto_create_monitor:
                if not job_name:
                    raise RuntimeError("A job name is required when auto-creating Kuma monitors per backup job.")
                monitor_name = render_monitor_name(self._settings, job_name)
                if monitor_name not in self._push_urls_by_monitor:
                    description = render_monitor_description(self._settings, job_name)
                    self._push_urls_by_monitor[monitor_name] = KumaProvisioner(self._settings).ensure_push_monitor(
                        monitor_name=monitor_name,
                        description=description,
                    )
                return self._push_urls_by_monitor[monitor_name]

            if not self._push_url:
                raise RuntimeError("No Kuma push URL is configured or could be provisioned.")

            return self._push_url
