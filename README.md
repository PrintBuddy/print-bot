# print-buddy-bot

Telegram admin interface for [print-buddy](https://github.com/rbenatuilv/print-buddy) — recharge/adjust user balances, list users, and approve/reject recharge requests, all from Telegram. Talks to the backend's `/telegram/*` API.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt   # or requirements.txt for a production install
cp .env.example .env
# fill in TELEGRAM_TOKEN, TELEGRAM_SECRET, API_BASE_URL
python -m src.main
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | yes | Bot token from [@BotFather](https://t.me/BotFather). |
| `TELEGRAM_SECRET` | yes | Shared secret sent as `X-Telegram-Secret` on every backend request. Must match the backend's own `TELEGRAM_SECRET`. |
| `API_BASE_URL` | no (default `http://localhost:8000/api`) | Base URL of the backend API. |
| `API_TIMEOUT` | no (default `5`) | Per-request timeout in seconds. |
| `ADMIN_CHAT_ID` | no | Not currently used to gate any command; kept for reference. |

Admin access itself is controlled entirely by the backend — a Telegram chat ID must be registered as an admin there (see backend's `/api/settings/telegram-admins`) before any command in this bot will succeed for that chat.

## Commands

- `/start` — welcome message; shows the admin or user command list depending on registration.
- `/myid` — show the caller's Telegram chat ID (needed to register as an admin).
- `/users` — list all users (admin only).
- `/user <username>` — show a user's balance (admin only).
- `/recharge <username> <amount>` — add credit to a user's balance (admin only).
- `/adjust <username> <new_balance>` — set a user's balance to an absolute value (admin only).
- `/request_recharge <username> <amount> [message]` — any user can request a recharge; registered admins are notified and can approve/reject via inline buttons.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Deploy

`scripts/deploy.sh` builds and runs the bot in Docker. It currently hardcodes a deployment path and expects `TELEGRAM_TOKEN`/`TELEGRAM_SECRET` to already be available to the container's environment — treat it as a starting point, not a finished deploy pipeline.
