from __future__ import annotations

import base64
import hmac
import html
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from .config import Settings
from .state import State

LOGGER = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _phase_label(phase: str) -> str:
    labels = {
        "starting": "Starter op",
        "polling": "Henter data",
        "sleeping": "Venter",
        "idle": "Afsluttet",
        "error": "Fejl",
    }
    return labels.get(phase, phase)


@dataclass
class RuntimeSnapshot:
    started_at: Optional[str]
    phase: str
    phase_label: str
    activity: str
    next_poll_due_at: Optional[str]
    last_cycle_started_at: Optional[str]
    last_cycle_completed_at: Optional[str]
    last_cycle_duration_seconds: Optional[float]
    last_fetch_count: int
    last_relevant_count: int
    last_processed_count: int
    last_message_subject: Optional[str]
    last_message_received_at: Optional[str]
    manual_poll_pending: bool
    last_manual_poll_requested_at: Optional[str]
    last_error: Optional[str]
    last_error_at: Optional[str]


class RuntimeStatus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = _utcnow()
        self._phase = "starting"
        self._activity = "Service starter."
        self._next_poll_due_at: Optional[datetime] = None
        self._last_cycle_started_at: Optional[datetime] = None
        self._last_cycle_completed_at: Optional[datetime] = None
        self._last_cycle_duration_seconds: Optional[float] = None
        self._last_fetch_count = 0
        self._last_relevant_count = 0
        self._last_processed_count = 0
        self._last_message_subject: Optional[str] = None
        self._last_message_received_at: Optional[datetime] = None
        self._manual_poll_pending = False
        self._last_manual_poll_requested_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._last_error_at: Optional[datetime] = None

    def mark_phase(self, phase: str, activity: str) -> None:
        with self._lock:
            self._phase = phase
            self._activity = activity

    def start_cycle(self) -> None:
        with self._lock:
            self._phase = "polling"
            self._activity = "Henter nye e-mails fra Microsoft 365."
            self._next_poll_due_at = None
            self._last_cycle_started_at = _utcnow()
            self._last_fetch_count = 0
            self._last_relevant_count = 0
            self._last_processed_count = 0
            self._manual_poll_pending = False

    def record_fetch(self, *, total_messages: int, relevant_messages: int) -> None:
        with self._lock:
            self._phase = "polling"
            self._activity = "Analyserer modtagne e-mails."
            self._last_fetch_count = total_messages
            self._last_relevant_count = relevant_messages

    def record_message(self, *, subject: str, received_at: datetime, activity: str) -> None:
        with self._lock:
            self._phase = "polling"
            self._activity = activity
            self._last_message_subject = subject
            self._last_message_received_at = received_at

    def complete_cycle(self, *, processed_count: int, activity: str) -> None:
        finished_at = _utcnow()
        with self._lock:
            self._phase = "polling"
            self._activity = activity
            self._last_cycle_completed_at = finished_at
            self._last_processed_count = processed_count
            if self._last_cycle_started_at is not None:
                duration = finished_at - self._last_cycle_started_at
                self._last_cycle_duration_seconds = round(duration.total_seconds(), 3)

    def record_error(self, exc: Exception) -> None:
        with self._lock:
            self._phase = "error"
            self._activity = "Seneste polling sluttede med en fejl."
            self._last_error = str(exc)
            self._last_error_at = _utcnow()

    def schedule_next_poll(self, *, after_seconds: int, activity: str) -> None:
        with self._lock:
            self._next_poll_due_at = _utcnow().replace(microsecond=0)
            self._next_poll_due_at = self._next_poll_due_at.fromtimestamp(
                self._next_poll_due_at.timestamp() + after_seconds,
                tz=timezone.utc,
            )
            if self._phase != "error":
                self._phase = "sleeping"
            self._activity = activity

    def clear_next_poll(self) -> None:
        with self._lock:
            self._next_poll_due_at = None

    def record_manual_poll_request(self) -> None:
        with self._lock:
            self._manual_poll_pending = True
            self._last_manual_poll_requested_at = _utcnow()

    def manual_poll_received(self) -> None:
        with self._lock:
            self._next_poll_due_at = None
            if self._phase != "error":
                self._phase = "sleeping"
            self._activity = "Manuel polling er modtaget og starter nu."

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return RuntimeSnapshot(
                started_at=_to_iso(self._started_at),
                phase=self._phase,
                phase_label=_phase_label(self._phase),
                activity=self._activity,
                next_poll_due_at=_to_iso(self._next_poll_due_at),
                last_cycle_started_at=_to_iso(self._last_cycle_started_at),
                last_cycle_completed_at=_to_iso(self._last_cycle_completed_at),
                last_cycle_duration_seconds=self._last_cycle_duration_seconds,
                last_fetch_count=self._last_fetch_count,
                last_relevant_count=self._last_relevant_count,
                last_processed_count=self._last_processed_count,
                last_message_subject=self._last_message_subject,
                last_message_received_at=_to_iso(self._last_message_received_at),
                manual_poll_pending=self._manual_poll_pending,
                last_manual_poll_requested_at=_to_iso(self._last_manual_poll_requested_at),
                last_error=self._last_error,
                last_error_at=_to_iso(self._last_error_at),
            )


def build_status_snapshot(settings: Settings, runtime_status: RuntimeStatus) -> Dict[str, Any]:
    runtime = runtime_status.snapshot()
    state_error: Optional[str] = None
    try:
        state = State.load(settings.state_file)
    except Exception as exc:
        LOGGER.warning("Could not load state file for monitor page.", exc_info=True)
        state = State()
        state_error = str(exc)

    service_status = state.last_pushed_status or "unknown"
    if runtime.phase == "error":
        service_status = "error"

    return {
        "title": settings.monitor_title,
        "mailbox": settings.mailbox,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "state_file": str(settings.state_file),
        "service_status": service_status,
        "runtime": {
            "started_at": runtime.started_at,
            "phase": runtime.phase,
            "phase_label": runtime.phase_label,
            "activity": runtime.activity,
            "next_poll_due_at": runtime.next_poll_due_at,
            "last_cycle_started_at": runtime.last_cycle_started_at,
            "last_cycle_completed_at": runtime.last_cycle_completed_at,
            "last_cycle_duration_seconds": runtime.last_cycle_duration_seconds,
            "last_fetch_count": runtime.last_fetch_count,
            "last_relevant_count": runtime.last_relevant_count,
            "last_processed_count": runtime.last_processed_count,
            "last_message_subject": runtime.last_message_subject,
            "last_message_received_at": runtime.last_message_received_at,
            "manual_poll_pending": runtime.manual_poll_pending,
            "last_manual_poll_requested_at": runtime.last_manual_poll_requested_at,
            "last_error": runtime.last_error,
            "last_error_at": runtime.last_error_at,
        },
        "state": {
            "last_seen_received_at": _to_iso(state.last_seen_received_at),
            "last_pushed_status": state.last_pushed_status,
            "last_pushed_summary": state.last_pushed_summary,
            "last_pushed_at": _to_iso(state.last_pushed_at),
            "processed_message_count": len(state.processed_message_ids),
        },
        "manual_poll_available": not settings.once,
        "state_error": state_error,
    }


def render_monitor_html(snapshot: Dict[str, Any]) -> str:
    runtime = snapshot["runtime"]
    state = snapshot["state"]
    status_class = html.escape(str(snapshot["service_status"]))
    next_poll_due_at = runtime["next_poll_due_at"] or ""
    countdown_initial = "Ikke planlagt" if not next_poll_due_at else ""
    manual_button_state = "disabled" if not snapshot["manual_poll_available"] else ""
    manual_button_text = "Manuel polling er ikke tilgængelig i --once mode" if not snapshot["manual_poll_available"] else "Kør manuel poll nu"
    manual_poll_note = "Ja" if runtime["manual_poll_pending"] else "Nej"

    def line(label: str, value: Any) -> str:
        rendered = "Ikke endnu" if value in (None, "") else html.escape(str(value))
        return (
            "<div class='item'>"
            f"<span class='label'>{html.escape(label)}</span>"
            f"<span class='value'>{rendered}</span>"
            "</div>"
        )

    return f"""<!doctype html>
<html lang="da">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="15">
  <title>{html.escape(str(snapshot["title"]))}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3efe7;
      --panel: rgba(255, 255, 255, 0.9);
      --ink: #1f2933;
      --muted: #52606d;
      --border: rgba(31, 41, 51, 0.12);
      --up: #1f7a4f;
      --down: #b33939;
      --pending: #b7791f;
      --unknown: #5b6470;
      --error: #8f2d2d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.85), transparent 38%),
        linear-gradient(135deg, #ece4d8 0%, #d7e7ef 100%);
      min-height: 100vh;
    }}
    .shell {{
      width: min(1080px, calc(100% - 32px));
      margin: 32px auto;
      display: grid;
      gap: 20px;
    }}
    .hero, .grid > section {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 24px;
      backdrop-filter: blur(12px);
      box-shadow: 0 16px 40px rgba(31, 41, 51, 0.08);
    }}
    .hero {{
      display: grid;
      gap: 14px;
    }}
    h1, h2, p {{ margin: 0; }}
    .eyebrow {{
      font-size: 0.85rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .status {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      width: fit-content;
      padding: 10px 16px;
      border-radius: 999px;
      background: rgba(255,255,255,0.9);
      border: 1px solid var(--border);
      font-weight: 700;
    }}
    .dot {{
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: var(--unknown);
    }}
    .status.up .dot {{ background: var(--up); }}
    .status.down .dot {{ background: var(--down); }}
    .status.pending .dot {{ background: var(--pending); }}
    .status.error .dot {{ background: var(--error); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 20px;
    }}
    .stack {{
      display: grid;
      gap: 12px;
      margin-top: 18px;
    }}
    .item {{
      display: grid;
      gap: 4px;
      padding-bottom: 12px;
      border-bottom: 1px solid rgba(31, 41, 51, 0.08);
    }}
    .item:last-child {{ border-bottom: 0; padding-bottom: 0; }}
    .label {{
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
    }}
    .value {{
      font-size: 1rem;
      font-weight: 600;
      word-break: break-word;
    }}
    .summary {{
      font-size: 1.1rem;
      line-height: 1.5;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      margin-top: 8px;
    }}
    .button {{
      appearance: none;
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      background: #1f2933;
      color: white;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}
    .button[disabled] {{
      opacity: 0.45;
      cursor: not-allowed;
    }}
    .meta {{
      color: var(--muted);
      font-size: 0.95rem;
    }}
    @media (max-width: 640px) {{
      .shell {{ width: min(100% - 20px, 1080px); margin: 20px auto; }}
      .hero, .grid > section {{ padding: 20px; border-radius: 20px; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <span class="eyebrow">Driftsstatus</span>
      <h1>{html.escape(str(snapshot["title"]))}</h1>
      <div class="status {status_class}">
        <span class="dot"></span>
        <span>{html.escape(str(runtime["phase_label"]))} / {html.escape(str(snapshot["service_status"]))}</span>
      </div>
      <p class="summary">{html.escape(str(runtime["activity"]))}</p>
      <div class="actions">
        <form method="post" action="/api/poll">
          <button class="button" type="submit" {manual_button_state}>{html.escape(manual_button_text)}</button>
        </form>
        <span class="meta">Næste automatiske poll: <strong id="next-poll-countdown">{countdown_initial}</strong></span>
      </div>
      {f"<p class='summary'>State-fil kunne ikke læses: {html.escape(snapshot['state_error'])}</p>" if snapshot["state_error"] else ""}
    </section>
    <div class="grid">
      <section>
        <h2>System</h2>
        <div class="stack">
          {line("Mailbox", snapshot["mailbox"])}
          {line("Poll interval", f"{snapshot['poll_interval_seconds']} sekunder")}
          {line("Næste poll kl.", runtime["next_poll_due_at"])}
          {line("Manuel poll afventer", manual_poll_note)}
          {line("Sidste manuelle klik", runtime["last_manual_poll_requested_at"])}
          {line("Startet", runtime["started_at"])}
          {line("State-fil", snapshot["state_file"])}
        </div>
      </section>
      <section>
        <h2>Seneste polling</h2>
        <div class="stack">
          {line("Polling startet", runtime["last_cycle_started_at"])}
          {line("Polling afsluttet", runtime["last_cycle_completed_at"])}
          {line("Varighed", f"{runtime['last_cycle_duration_seconds']} sekunder" if runtime["last_cycle_duration_seconds"] is not None else None)}
          {line("Hentede e-mails", runtime["last_fetch_count"])}
          {line("Relevante e-mails", runtime["last_relevant_count"])}
          {line("Behandlede e-mails", runtime["last_processed_count"])}
        </div>
      </section>
      <section>
        <h2>Seneste backup-status</h2>
        <div class="stack">
          {line("Kuma-status", state["last_pushed_status"])}
          {line("Opsummering", state["last_pushed_summary"])}
          {line("Sendt til Kuma", state["last_pushed_at"])}
          {line("Sidst sete e-mail", runtime["last_message_subject"])}
          {line("E-mail modtaget", runtime["last_message_received_at"])}
        </div>
      </section>
      <section>
        <h2>Fejl og cache</h2>
        <div class="stack">
          {line("Seneste fejl", runtime["last_error"])}
          {line("Fejl tidspunkt", runtime["last_error_at"])}
          {line("Cachede message ids", state["processed_message_count"])}
          {line("Sidst sete received_at", state["last_seen_received_at"])}
        </div>
      </section>
    </div>
  </main>
  <script>
    (function () {{
      const target = document.getElementById("next-poll-countdown");
      const dueAtRaw = {json.dumps(next_poll_due_at)};
      if (!target || !dueAtRaw) {{
        if (target && !target.textContent) {{
          target.textContent = "Ikke planlagt";
        }}
        return;
      }}

      const dueAt = new Date(dueAtRaw);
      const format = (seconds) => {{
        if (seconds <= 0) {{
          return "Starter nu";
        }}
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        if (mins === 0) {{
          return `${{secs}} sek`;
        }}
        return `${{mins}}m ${{secs.toString().padStart(2, "0")}}s`;
      }};

      const tick = () => {{
        const diffSeconds = Math.max(0, Math.floor((dueAt.getTime() - Date.now()) / 1000));
        target.textContent = format(diffSeconds);
      }};

      tick();
      window.setInterval(tick, 1000);
    }})();
  </script>
</body>
</html>
"""


class _MonitorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        settings: Settings,
        runtime_status: RuntimeStatus,
        manual_poll_callback: Optional[Callable[[], bool]],
    ) -> None:
        super().__init__(server_address, _MonitorRequestHandler)
        self.settings = settings
        self.runtime_status = runtime_status
        self.manual_poll_callback = manual_poll_callback


class _MonitorRequestHandler(BaseHTTPRequestHandler):
    server: _MonitorHTTPServer

    def do_GET(self) -> None:  # noqa: N802
        if not self._is_authorized():
            self._require_auth()
            return

        parsed = urlparse(self.path)
        if parsed.path in {"", "/"}:
            snapshot = build_status_snapshot(self.server.settings, self.server.runtime_status)
            body = render_monitor_html(snapshot).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/status":
            payload = json.dumps(
                build_status_snapshot(self.server.settings, self.server.runtime_status),
                indent=2,
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        if not self._is_authorized():
            self._require_auth()
            return

        parsed = urlparse(self.path)
        if parsed.path != "/api/poll":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        if self.server.manual_poll_callback is None:
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "Manual poll not available")
            return

        triggered = self.server.manual_poll_callback()
        if "application/json" in self.headers.get("Accept", ""):
            payload = json.dumps({"ok": triggered}, indent=2).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("Monitor page: " + format, *args)

    def _is_authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False

        try:
            raw = base64.b64decode(header[6:], validate=True).decode("utf-8")
        except Exception:
            return False

        username, separator, password = raw.partition(":")
        if not separator:
            return False

        expected_username = self.server.settings.monitor_username or ""
        expected_password = self.server.settings.monitor_password or ""
        return hmac.compare_digest(username, expected_username) and hmac.compare_digest(password, expected_password)

    def _require_auth(self) -> None:
        body = b"Authentication required"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="monitorinbox2kuma"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MonitorServer:
    def __init__(
        self,
        settings: Settings,
        runtime_status: RuntimeStatus,
        manual_poll_callback: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._settings = settings
        self._runtime_status = runtime_status
        self._manual_poll_callback = manual_poll_callback
        self._server: Optional[_MonitorHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self._settings.monitor_enabled:
            return
        if self._server is not None:
            return

        self._server = _MonitorHTTPServer(
            (self._settings.monitor_host, self._settings.monitor_port),
            self._settings,
            self._runtime_status,
            self._manual_poll_callback,
        )
        self._thread = threading.Thread(target=self._server.serve_forever, name="monitor-status-server", daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        LOGGER.info("Monitor page listening on http://%s:%s", host, port)

    def stop(self) -> None:
        if self._server is None:
            return

        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

        self._server = None
        self._thread = None

    @property
    def port(self) -> Optional[int]:
        if self._server is None:
            return None
        return int(self._server.server_address[1])
