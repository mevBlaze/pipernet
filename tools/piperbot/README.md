# Piperbot

Piperbot is the official Telegram bridge for the Pied Piper community group.
It does two things:

1. **Bot commands** — lets community members mint a Pipernet identity (`.dot.png`), verify each other's dots, and check protocol status.
2. **Bridge mode** — signs every normal group message as a Pipernet envelope and appends it to `~/.pipernet/channels/telegram-bridge.jsonl`. The Telegram group _becomes_ a Pipernet channel: append-only, cryptographically auditable, signed by the bot as `compute_proxy`.

---

## Privacy notice

**By chatting in the Pied Piper Telegram group, your messages are signed by piperbot and added to the public Pipernet channel `telegram-bridge`. If you do not want this, leave the group.**

Only message text is bridged. Attachments (photos, files, stickers) are not captured. Message content is never echoed to server logs unless `--debug` is explicitly set.

---

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message + orientation |
| `/dot` | Mint your Pipernet identity (sends `.dot.png` back to you) |
| `/scan` | Verify a `.dot.png` (reply to a photo message, then type `/scan`) |
| `/whoami` | Show your registered handle + pubkey |
| `/status` | Latest commit, GitHub stars, bridge envelope count, dot minters |
| `/help` | Full command list |

No price commands. No token shilling. This bot is about the protocol.

---

## Requirements

```
Python 3.10+
python-telegram-bot>=22.0
aiohttp>=3.9
cryptography>=42.0

# For /dot and /scan (optional but recommended):
qrcode[pil]>=7.0
Pillow>=10.0
pyzbar>=0.1.9
```

Install all at once:

```bash
pip install python-telegram-bot>=22.0 aiohttp>=3.9 cryptography>=42.0
pip install 'qrcode[pil]>=7.0' Pillow>=10.0 pyzbar>=0.1.9
```

Or via this package:

```bash
cd tools/piperbot
pip install -e '.[dot]'
```

---

## Config

### File-based (recommended)

Copy `config.example.json` to `~/.pipernet/piperbot.json`:

```bash
cp tools/piperbot/config.example.json ~/.pipernet/piperbot.json
$EDITOR ~/.pipernet/piperbot.json
```

Fill in:

```json
{
  "telegram_bot_token": "<from BotFather>",
  "group_chat_id": "<negative number>",
  "bridge_channel": "telegram-bridge",
  "github_repo": "dot-protocol/pipernet"
}
```

**Never commit this file.** `~/.pipernet/piperbot.json` is outside the repo.

### Environment variables (override file values)

| Env var | Config key |
|---------|-----------|
| `TELEGRAM_BOT_TOKEN` | `telegram_bot_token` |
| `TELEGRAM_GROUP_CHAT_ID` | `group_chat_id` |
| `PIPERBOT_BRIDGE_CHANNEL` | `bridge_channel` |
| `PIPERBOT_GITHUB_REPO` | `github_repo` |

### Finding your group_chat_id

1. Add the bot to the group.
2. Send any message in the group.
3. Call `https://api.telegram.org/bot<TOKEN>/getUpdates`.
4. Look for `"chat": {"id": -100xxxxxxxxx}` — that negative number is your `group_chat_id`.

---

## Running

### Via pipernet CLI (recommended)

```bash
# Start the bot (reads ~/.pipernet/piperbot.json by default)
pipernet bot

# Custom config path
pipernet bot --config /path/to/piperbot.json

# Verbose debug logging
pipernet bot --debug

# Dry-run (no Telegram connection — for testing/CI)
pipernet bot --dry-run
```

### Directly

```bash
python -m tools.piperbot.main  # if running from repo root
```

---

## Dry-run mode (CI / testing without a real token)

```bash
pipernet bot --dry-run
```

Output:

```
=== piperbot dry-run mode ===
bridge_channel: telegram-bridge
github_repo: dot-protocol/pipernet
bot_handle: piperbot

simulating message from @testuser: 'hello from the dry-run simulation'

=== sample envelope ===
{
  "body": [...],
  "channel": "telegram-bridge",
  "compute_proxy": "piperbot",
  ...
  "signature": "..."
}

=== self-verify: True ===
bridge channel 'telegram-bridge' has 1 envelopes total

dry-run complete. no Telegram connection made.
```

No bot token required for dry-run. The piperbot keypair is generated automatically on first run.

---

## Testing (manual — with a real bot token)

1. Create a bot via `@BotFather` → `/newbot`.
2. Get a group chat ID (see above).
3. Fill in `~/.pipernet/piperbot.json`.
4. Run `pipernet bot` and leave it running.
5. In the Telegram group: send `/start`, `/dot`, `/help`.
6. For `/scan`: upload a `.dot.png` to the group, then reply to it with `/scan`.
7. Send a normal message — check `~/.pipernet/channels/telegram-bridge.jsonl` to confirm it was bridged.
8. Run `pipernet inbox --channel telegram-bridge` to verify envelopes.

---

## Deploy: local (long-running process)

```bash
pipernet bot &
```

Or in a `tmux` session:

```bash
tmux new -s piperbot
pipernet bot
# Ctrl+B, D to detach
```

## Deploy: systemd (VPS)

Create `/etc/systemd/system/piperbot.service`:

```ini
[Unit]
Description=Piperbot — Telegram ↔ Pipernet bridge
After=network.target

[Service]
Type=simple
User=piperbot
WorkingDirectory=/opt/pipernet
ExecStart=/usr/local/bin/pipernet bot
Restart=always
RestartSec=5
Environment=TELEGRAM_BOT_TOKEN=<your-token>
Environment=TELEGRAM_GROUP_CHAT_ID=<your-chat-id>
# Optional: keep logs private
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable piperbot
systemctl start piperbot
systemctl status piperbot
```

## Deploy: fly.io

Create `tools/piperbot/Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -e /app/tools/piperbot[dot] -e /app
# Expect token via FLY_SECRET: fly secrets set TELEGRAM_BOT_TOKEN=...
CMD ["pipernet", "bot"]
```

```bash
fly launch --dockerfile tools/piperbot/Dockerfile
fly secrets set TELEGRAM_BOT_TOKEN=<token> TELEGRAM_GROUP_CHAT_ID=<id>
fly deploy
```

**Recommended deploy target:** fly.io for zero-ops production, systemd on your existing VPS for maximum control. The bot is a single long-running async process — one instance per group.

### Scaling note

Do not run multiple piperbot instances for the same Telegram group. Telegram delivers each update to only one `getUpdates` poller. If you need redundancy, use webhooks (`app.run_webhook(...)`) behind a load balancer — but for a single community group, one process is correct.

---

## Bridge channel format

Each message in `~/.pipernet/channels/telegram-bridge.jsonl` is one signed envelope per line:

```json
{
  "body": [
    ["txt", "hello from pied piper"],
    ["telegram", {"chat_id": -1001234567890, "message_id": 42, "username": "dinesh", "user_id": 987654321}]
  ],
  "channel": "telegram-bridge",
  "compute_proxy": "piperbot",
  "from": "piperbot",
  "modes": ["telegram"],
  "parent": null,
  "sequence": 1,
  "signature": "...",
  "timestamp": "2026-04-30T12:00:00+00:00"
}
```

Inspect it:

```bash
pipernet inbox --channel telegram-bridge
```

Verify signatures:

```bash
pipernet verify <(pipernet inbox --channel telegram-bridge --json | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)[0]))")
```

---

## Architecture

```
Telegram group
    │  (every non-command text message)
    ▼
Piperbot (python-telegram-bot, async)
    │
    ├── signs as "piperbot" (Ed25519, compute_proxy)
    │
    ▼
~/.pipernet/channels/telegram-bridge.jsonl   (append-only)
    │
    ▼
pipernet inbox --channel telegram-bridge     (read)
pipernet verify <envelope.json>              (verify)
```

The bot identity (`piperbot` keypair) is generated once at `~/.pipernet/piperbot.private.bin` and never changes. All envelopes are signed by this key — the originating Telegram user is recorded in the `body[1]["telegram"]` tuple, not in the `from` field.
