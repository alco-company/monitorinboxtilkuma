# monitorinbox2kuma

Small service that watches a Microsoft 365 mailbox for Synology backup emails and forwards the result to an Uptime Kuma Push Monitor.

## What it does

1. Signs in to Microsoft Graph with app-only credentials.
2. Reads new emails from the selected mailbox and folder.
3. Matches the email subject and body against success and failure patterns.
4. Extracts the backup job name from the email subject.
5. Pushes `up` or `down` to the matching Uptime Kuma monitor for that job.
6. Stores local state so the same message is never processed twice.
7. Can optionally log in to Uptime Kuma and create the Push monitor automatically.

## Architecture

The current implementation uses polling because it is simple to operate and works well for a dedicated monitor mailbox. If Synology sends one success email per backup run, Uptime Kuma can also detect missing emails through the Push Monitor heartbeat window.

## Requirements

- Microsoft 365 / Exchange Online mailbox, for example `monitor@al.dk`
- Entra ID app registration with Graph access
- Uptime Kuma Push Monitor URL, or a Kuma login that can create one
- Python 3.9+ or Docker

## Microsoft 365 setup

1. Create an app registration in Microsoft Entra ID.
2. Add the Microsoft Graph application permission `Mail.ReadWrite`.
3. Grant admin consent to the app.
4. For security, scope the app to only the monitor mailbox by using Exchange Online RBAC for Applications or a legacy Application Access Policy.
5. Create a client secret and store the `tenant id`, `client id`, and `client secret`.

## Uptime Kuma setup

You now have two choices:

1. Manual: create a monitor of type `Push`, then copy its Push URL into `KUMA_PUSH_URL`.
2. Automatic: give the service a Kuma URL plus either a JWT token or a username and password, and let it create or reuse the monitor itself.

If you use automatic mode, the service looks for a monitor name generated from `KUMA_MONITOR_NAME_TEMPLATE`. If it does not exist, it creates it as type `Push`.

Set the heartbeat interval so it matches how often the backup is expected to report in.

Examples:

- Daily backup: heartbeat around `26h`
- Hourly backup: heartbeat around `70m`

## Configuration

Copy `.env.example` to `.env` and fill in the values.

Important variables:

- `M365_MAILBOX`: mailbox to monitor
- `M365_ALLOWED_SENDERS`: comma-separated list of senders that are allowed
- `KUMA_PUSH_URL`: Push URL from Uptime Kuma when using manual mode
- `KUMA_AUTO_CREATE_MONITOR`: set to `true` to let the service create or reuse the monitor itself
- `KUMA_BASE_URL`: Kuma URL. `https://kuma.example.com/dashboard` is accepted and normalized automatically
- `KUMA_JWT_TOKEN`: JWT from a previous Kuma login with `Remember Me`
- `KUMA_MONITOR_NAME_TEMPLATE`: monitor name template, for example `Synology Backup - {job_name}`
- `SUCCESS_PATTERNS`: optional JSON array of regex patterns for success
- `FAILURE_PATTERNS`: optional JSON array of regex patterns for failure

Example for auto-create mode with JWT:

```env
KUMA_AUTO_CREATE_MONITOR=true
KUMA_BASE_URL=https://kuma.alco.company/dashboard
KUMA_JWT_TOKEN=eyJ...
KUMA_MONITOR_NAME_TEMPLATE=Synology Backup - {job_name}
KUMA_MONITOR_INTERVAL_SECONDS=93600
KUMA_MONITOR_RETRY_INTERVAL_SECONDS=600
```

If you prefer, you can still use `KUMA_USERNAME` and `KUMA_PASSWORD` instead of `KUMA_JWT_TOKEN`.

Example with more realistic Synology patterns:

```env
M365_ALLOWED_SENDERS=monitor@pbox.dk
SUCCESS_PATTERNS=["completed successfully","er fuldført","completed backup of"]
FAILURE_PATTERNS=["delvist gennemført","\\bfailed\\b","\\berror\\b","destination disk is full","backup task.*aborted"]
```

With your example subjects, the service will create monitors like:

- `Synology Backup - ABB Teksam-Default @ adslthi.alco.dk`
- `Synology Backup - ABB DanaVinduer-Default @ adslthi.alco.dk`
- `Synology Backup - Backup Virtualmin @ hosting-2.alco.company`
- `Synology Backup - M365 NordTHY A/S`
- `Synology Backup - M365 Danavinduer`

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .[dev]
cp .env.example .env
monitorinbox2kuma --once
monitorinbox2kuma
```

## Docker

```bash
cp .env.example .env
docker compose up -d --build
```

The state file is written to `./data/state.json` by default.

## Runtime behavior

- First run: the service takes the newest relevant email and pushes its status as the initial state.
- Later runs: only new emails are processed.
- If an email does not match your patterns, it is ignored.
- In auto-create mode, the service creates or reuses one Push monitor per parsed backup job.
- After a relevant email is processed, the service deletes it from Inbox by calling Microsoft Graph `DELETE /users/{mailbox}/messages/{id}`. In Exchange Online this removes it from Inbox rather than using the separate `permanentDelete` action.

## Production tips

- Use a dedicated mailbox with low traffic.
- Filter on sender so unrelated emails never reach the parser.
- Tune the regex patterns from real Synology emails.
- Run it in Docker on an integration host, or from cron with `--once`.
- Because monitor creation uses Kuma's internal Socket.IO API, keep an eye on Kuma upgrades.
