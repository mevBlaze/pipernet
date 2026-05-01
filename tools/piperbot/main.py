"""
tools/piperbot/main.py — Piperbot: Telegram ↔ Pipernet bridge bot

Every message in the configured group becomes a signed Pipernet envelope
in the `telegram-bridge` channel. Cryptographically auditable. Append-only.

Commands:
    /start     welcome + orientation
    /dot       mint Pipernet identity (sends .dot.png to user)
    /scan      scan a .dot.png image (reply to photo message)
    /whoami    show user's registered handle + pubkey
    /status    repo commit, GitHub stars, envelope count, dot minters
    /help      full command list

Bridge mode:
    Every normal group message → signed envelope → telegram-bridge.jsonl

Privacy: message content is NOT echoed to logs unless --debug is set.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Dependency guard ──────────────────────────────────────────────────────────

def _require(package: str, install: str) -> None:
    import importlib
    try:
        importlib.import_module(package)
    except ImportError:
        print(
            f"error: '{package}' not installed.\n"
            f"  pip install {install}",
            file=sys.stderr,
        )
        sys.exit(1)


_require("telegram", "python-telegram-bot>=22.0")
_require("aiohttp", "aiohttp>=3.9")

from telegram import Update, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)
from telegram.error import Forbidden, BadRequest

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_LEVEL = logging.DEBUG if os.environ.get("PIPERBOT_DEBUG") else logging.INFO
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=LOG_LEVEL,
)
log = logging.getLogger("piperbot")

# ── Config ────────────────────────────────────────────────────────────────────

def _pipernet_home() -> Path:
    base = os.environ.get("PIPERNET_HOME")
    p = Path(base) if base else Path.home() / ".pipernet"
    p.mkdir(parents=True, exist_ok=True)
    (p / "channels").mkdir(parents=True, exist_ok=True)
    return p


def load_config(config_path: Path | None = None) -> dict:
    """Load config from file, then layer environment variable overrides."""
    default_path = _pipernet_home() / "piperbot.json"
    path = config_path or default_path

    cfg: dict = {}
    if path.exists():
        cfg = json.loads(path.read_text())
    else:
        log.warning("config file not found at %s — using env vars only", path)

    # Environment variable overrides (always win over file values)
    overrides = {
        "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN"),
        "group_chat_id": os.environ.get("TELEGRAM_GROUP_CHAT_ID"),
        "bridge_channel": os.environ.get("PIPERBOT_BRIDGE_CHANNEL"),
        "github_repo": os.environ.get("PIPERBOT_GITHUB_REPO"),
    }
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v

    cfg.setdefault("bridge_channel", "telegram-bridge")
    cfg.setdefault("github_repo", "dot-protocol/pipernet")
    return cfg


# ── Pipernet core helpers ────────────────────────────────────────────────────
# We import from cli/core.py via the installed package (or relative path fallback).

def _load_core():
    """Return cli.core module, whether installed or discovered relative to this file."""
    try:
        import cli.core as core
        return core
    except ImportError:
        import importlib.util
        here = Path(__file__).resolve().parent.parent.parent
        spec = importlib.util.spec_from_file_location("cli_core", here / "cli" / "core.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


def _load_dot_encode():
    """Return tools/dot/encode module."""
    import importlib.util
    here = Path(__file__).resolve().parent.parent.parent
    spec = importlib.util.spec_from_file_location("dot_encode", here / "tools" / "dot" / "encode.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_dot_decode():
    """Return tools/dot/decode module."""
    import importlib.util
    here = Path(__file__).resolve().parent.parent.parent
    spec = importlib.util.spec_from_file_location("dot_decode", here / "tools" / "dot" / "decode.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Bot identity ─────────────────────────────────────────────────────────────

BOT_HANDLE = "piperbot"


def ensure_bot_keypair() -> None:
    """Generate piperbot's Ed25519 keypair on first run if not present."""
    core = _load_core()
    ks = core.keystore_path(BOT_HANDLE)
    if not ks.exists():
        log.info("generating piperbot keypair at %s", ks)
        core.generate_keypair(BOT_HANDLE)
        log.info("piperbot identity created")


# ── User handle registry ─────────────────────────────────────────────────────
# Maps Telegram user_id (int) → Pipernet handle ("tg-<username>")
# Stored in ~/.pipernet/tg_users.json

def _tg_users_path() -> Path:
    return _pipernet_home() / "tg_users.json"


def load_tg_users() -> dict[str, str]:
    p = _tg_users_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def save_tg_users(mapping: dict[str, str]) -> None:
    _tg_users_path().write_text(json.dumps(mapping, sort_keys=True, indent=2))


def tg_handle_for(user_id: int, username: str | None) -> str:
    """Return the Pipernet handle for this Telegram user."""
    slug = username.lower() if username else str(user_id)
    return f"tg-{slug}"


def get_or_register_tg_user(user_id: int, username: str | None) -> str:
    """Return handle from registry, or derive deterministically."""
    users = load_tg_users()
    key = str(user_id)
    if key not in users:
        handle = tg_handle_for(user_id, username)
        users[key] = handle
        save_tg_users(users)
    return users[key]


# ── Envelope factory ─────────────────────────────────────────────────────────

def _bridge_sequence(channel: str) -> int:
    """Count existing envelopes in bridge channel for monotonic sequence."""
    core = _load_core()
    entries = core.read_channel(channel)
    own = [e.get("sequence", 0) for e in entries if e.get("from") == BOT_HANDLE]
    return (max(own) + 1) if own else 1


def build_bridge_envelope(
    tg_username: str,
    tg_user_id: int,
    tg_chat_id: int,
    tg_message_id: int,
    text: str,
    channel: str,
) -> dict:
    """
    Build a signed Pipernet envelope from a Telegram message.
    Signed by piperbot's own keypair (compute_proxy pattern).
    """
    core = _load_core()
    sk = core.load_private_key(BOT_HANDLE)

    timestamp = datetime.now(timezone.utc).isoformat()
    sequence = _bridge_sequence(channel)

    envelope_unsigned = {
        "from": BOT_HANDLE,
        "channel": channel,
        "sequence": sequence,
        "parent": None,
        "modes": ["telegram"],
        "body": [
            ["txt", text],
            ["telegram", {
                "chat_id": tg_chat_id,
                "message_id": tg_message_id,
                "username": tg_username,
                "user_id": tg_user_id,
            }],
        ],
        "timestamp": timestamp,
        "compute_proxy": BOT_HANDLE,
    }

    signed = core.sign_envelope(envelope_unsigned, sk)
    return signed


# ── Dot minter counter ────────────────────────────────────────────────────────

def count_dot_minters() -> int:
    """Count Telegram users who have a dot (keypair in registry)."""
    core = _load_core()
    reg = core.load_pubkey_registry()
    tg_users = load_tg_users()
    handles_with_keys = set(reg.keys())
    count = sum(1 for handle in tg_users.values() if handle in handles_with_keys)
    return count


def count_bridge_envelopes(channel: str) -> int:
    core = _load_core()
    return len(core.read_channel(channel))


# ── GitHub stats (best-effort) ────────────────────────────────────────────────

async def fetch_github_stars(repo: str) -> str:
    """Return star count string, or '?' on any error."""
    import aiohttp
    url = f"https://api.github.com/repos/{repo}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return str(data.get("stargazers_count", "?"))
    except Exception:
        pass
    return "?"


def local_git_commit() -> str:
    """Return short HEAD commit hash from git log."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%h %s"],
            cwd=Path(__file__).resolve().parent.parent.parent,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()[:72]
    except Exception:
        pass
    return "unknown"


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "welcome to the grove. tap /dot to mint your Pipernet identity. tap /help for everything else."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "/dot — mint your Pipernet identity (.dot.png)\n"
        "/scan — verify a .dot.png (reply to a photo)\n"
        "/whoami — show your handle + pubkey\n"
        "/status — repo commit, stars, bridge stats\n"
        "/help — this list\n\n"
        "every message you send here is signed and added to the Pipernet `telegram-bridge` channel."
    )
    await update.message.reply_text(text)


async def cmd_dot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    handle = tg_handle_for(user.id, user.username)

    await update.message.reply_text(f"minting your identity as `{handle}`…", parse_mode="Markdown")

    try:
        core = _load_core()
        # Generate keypair only if not already done
        ks = core.keystore_path(handle)
        if not ks.exists():
            core.generate_keypair(handle)

        # Render the .dot.png
        dot_enc = _load_dot_encode()
        dot_path = dot_enc.generate_dot(handle)

        # Register user in our tg→handle map
        users = load_tg_users()
        users[str(user.id)] = handle
        save_tg_users(users)

        reg = core.load_pubkey_registry()
        pubkey_hex = reg.get(handle, "")

        caption = (
            f"handle: `{handle}`\n"
            f"pubkey: `{pubkey_hex[:16]}…`\n"
            f"tier: 0 (self-sovereign)\n\n"
            f"scan this with `/scan` to verify."
        )
        with open(dot_path, "rb") as f:
            await update.message.reply_photo(f, caption=caption, parse_mode="Markdown")

    except ImportError as e:
        await update.message.reply_text(
            f"dot generation requires optional deps: `pip install 'pipernet[dot]'`\nerror: {e}",
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error("cmd_dot error for user %s: %s", user.id, type(e).__name__)
        await update.message.reply_text(f"error minting dot: {type(e).__name__}")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /scan — user must REPLY to a message that contains a photo.
    Bot downloads the photo, runs dot scan, replies with verdict.
    """
    msg: Message = update.message

    # Find the photo — either in the replied-to message or the current one
    target_msg = msg.reply_to_message if msg.reply_to_message else msg

    if not target_msg or not target_msg.photo:
        await msg.reply_text(
            "reply to a message containing a .dot.png image, then use /scan"
        )
        return

    await msg.reply_text("scanning…")

    try:
        # Download highest-resolution version of the photo
        photo = target_msg.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        await tg_file.download_to_drive(tmp_path)

        # Run scan
        dot_dec = _load_dot_decode()
        code, result = dot_dec.scan_dot(tmp_path)

        tmp_path.unlink(missing_ok=True)

        if code == 0:
            text = (
                f"verified: true\n"
                f"handle: `{result.get('handle')}`\n"
                f"pubkey: `{result.get('pubkey_hex', '')[:16]}…`\n"
                f"tier: {result.get('tier')}"
            )
        elif code == 3:
            text = (
                f"verified: false\n"
                f"handle: `{result.get('handle')}`\n"
                f"reason: {result.get('reason', 'signature mismatch')}"
            )
        else:
            text = f"no Pipernet dot found in this image.\n{result.get('error', '')}"

        await msg.reply_text(text, parse_mode="Markdown")

    except ImportError as e:
        await msg.reply_text(
            f"scan requires optional deps: `pip install 'pipernet[dot]'`\nerror: {e}",
            parse_mode="Markdown",
        )
    except Exception as e:
        log.error("cmd_scan error: %s", e)
        await msg.reply_text(f"scan failed: {type(e).__name__}")


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    users = load_tg_users()
    handle = users.get(str(user.id))

    if not handle:
        await update.message.reply_text(
            "no Pipernet identity yet. use /dot to mint one."
        )
        return

    core = _load_core()
    reg = core.load_pubkey_registry()
    pubkey_hex = reg.get(handle)

    if not pubkey_hex:
        await update.message.reply_text(
            f"handle registered as `{handle}` but no pubkey found. run /dot to regenerate.",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        f"handle: `{handle}`\npubkey: `{pubkey_hex[:16]}…{pubkey_hex[-8:]}`",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data.get("config", {})
    repo = cfg.get("github_repo", "dot-protocol/pipernet")
    bridge = cfg.get("bridge_channel", "telegram-bridge")

    commit = local_git_commit()
    stars = await fetch_github_stars(repo)
    envelopes = count_bridge_envelopes(bridge)
    minters = count_dot_minters()

    text = (
        f"pipernet node status\n"
        f"commit: `{commit}`\n"
        f"github stars: {stars}\n"
        f"bridge envelopes: {envelopes}\n"
        f"dot minters: {minters}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── Bridge handler (normal group messages) ────────────────────────────────────

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Sign every non-command, non-bot message in the group as a Pipernet envelope.
    Append-only to the bridge channel JSONL.
    """
    msg = update.message
    if not msg or not msg.text:
        return

    user = msg.from_user
    if not user or user.is_bot:
        return

    cfg = context.bot_data.get("config", {})
    bridge_channel = cfg.get("bridge_channel", "telegram-bridge")

    try:
        username = user.username or str(user.id)
        envelope = build_bridge_envelope(
            tg_username=username,
            tg_user_id=user.id,
            tg_chat_id=msg.chat_id,
            tg_message_id=msg.message_id,
            text=msg.text,
            channel=bridge_channel,
        )
        core = _load_core()
        core.append_to_channel(bridge_channel, envelope)
        log.debug("bridged message_id=%s from %s → %s", msg.message_id, username, bridge_channel)
    except Exception as e:
        log.error("bridge failed for message_id=%s: %s", msg.message_id if msg else "?", e)


# ── New member welcome (DM) ───────────────────────────────────────────────────

async def handle_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    DM new group members with a welcome message.
    Silently skips if the user has DMs blocked.
    """
    result = update.chat_member
    if not result:
        return

    new_status = result.new_chat_member.status
    old_status = result.old_chat_member.status

    # Only trigger when someone actually joins (not just status changes)
    if new_status not in ("member", "administrator") or old_status in ("member", "administrator"):
        return

    user = result.new_chat_member.user
    if user.is_bot:
        return

    welcome = (
        "welcome to pied piper. type /dot in the group to mint your Pipernet identity.\n"
        "spec: piedpiper.fun\n"
        "repo: github.com/dot-protocol/pipernet"
    )

    try:
        await context.bot.send_message(chat_id=user.id, text=welcome)
        log.info("sent welcome DM to user_id=%s", user.id)
    except (Forbidden, BadRequest):
        # User has DMs blocked — fail silently as specified
        log.debug("could not DM welcome to user_id=%s (DMs blocked)", user.id)
    except Exception as e:
        log.warning("welcome DM failed for user_id=%s: %s", user.id, e)


# ── Dry-run mode ─────────────────────────────────────────────────────────────

def dry_run(config_path: Path | None = None) -> None:
    """
    Simulate bot message handling without connecting to Telegram.
    Useful for CI and local testing.
    """
    cfg = load_config(config_path)
    bridge_channel = cfg.get("bridge_channel", "telegram-bridge")

    print("=== piperbot dry-run mode ===")
    print(f"bridge_channel: {bridge_channel}")
    print(f"github_repo: {cfg.get('github_repo')}")
    print(f"bot_handle: {BOT_HANDLE}")
    print()

    # Ensure bot keypair exists
    ensure_bot_keypair()

    # Simulate a fake message
    fake_username = "testuser"
    fake_user_id = 12345678
    fake_chat_id = -1001234567890
    fake_message_id = 42
    fake_text = "hello from the dry-run simulation"

    print(f"simulating message from @{fake_username}: '{fake_text}'")

    envelope = build_bridge_envelope(
        tg_username=fake_username,
        tg_user_id=fake_user_id,
        tg_chat_id=fake_chat_id,
        tg_message_id=fake_message_id,
        text=fake_text,
        channel=bridge_channel,
    )

    print()
    print("=== sample envelope ===")
    print(json.dumps(envelope, indent=2))
    print()

    # Verify the envelope
    core = _load_core()
    verdict = core.verify_envelope(envelope)
    print(f"=== self-verify: {verdict['valid']} ===")
    if not verdict["valid"]:
        print(f"reason: {verdict['reason']}")

    # Show bridge stats
    count = count_bridge_envelopes(bridge_channel)
    print(f"bridge channel '{bridge_channel}' has {count} envelopes total")
    print()
    print("dry-run complete. no Telegram connection made.")


# ── Application entrypoint ────────────────────────────────────────────────────

def run(config_path: Path | None = None, dry: bool = False, debug: bool = False) -> None:
    if debug:
        os.environ["PIPERBOT_DEBUG"] = "1"
        logging.getLogger().setLevel(logging.DEBUG)

    if dry:
        dry_run(config_path)
        return

    cfg = load_config(config_path)

    token = cfg.get("telegram_bot_token")
    if not token:
        print(
            "error: no telegram_bot_token found.\n"
            "  set it in ~/.pipernet/piperbot.json or via TELEGRAM_BOT_TOKEN env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    group_chat_id = cfg.get("group_chat_id")
    if group_chat_id:
        log.info("bridging group_chat_id=%s", group_chat_id)

    # Ensure bot identity exists before starting
    ensure_bot_keypair()

    app = Application.builder().token(token).build()
    app.bot_data["config"] = cfg

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("dot", cmd_dot))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("status", cmd_status))

    # Bridge: non-command text messages in groups
    group_filter = filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS
    app.add_handler(MessageHandler(group_filter, handle_group_message))

    # New member DM welcome
    app.add_handler(ChatMemberHandler(handle_new_member, ChatMemberHandler.CHAT_MEMBER))

    log.info("piperbot starting (python-telegram-bot async)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
