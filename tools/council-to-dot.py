#!/usr/bin/env python3
"""
council-to-dot.py
=================
Reads a Claude Code session transcript (JSONL) and converts it into
signed Pipernet DOT envelopes stored in a local channel log.

Each human/assistant message in the session becomes a signed envelope
in channel  council-<session-id>  under ~/.pipernet/channels/.

Usage:
    python tools/council-to-dot.py [path-to-session.jsonl]

If no argument is given, the most-recently-modified *.jsonl under
~/.claude/projects/-Users-blaze-Movies-Kin/  is used automatically.

Output (to stdout):
    Processed  N  messages  →  M  envelopes
    Channel    ~/.pipernet/channels/council-<session-id>.jsonl
    Handle     council-<session-id>   pubkey=<hex>

The keypair for the session is written once to:
    ~/.pipernet/sessions/<session-id>.bin   (600 permissions)

Signing uses Ed25519 via pipernet.cli.core — same code that powers
the reference CLI.  Envelopes are schema v2.0, text mode.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: make sure the package root is on sys.path so we can import
# cli.core whether the script is run from inside tools/ or from the repo root.
# ---------------------------------------------------------------------------
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from cli.core import (  # noqa: E402
    append_to_channel,
    home as pipernet_home,
    next_sequence,
    register_pubkey,
    sign_envelope,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SESSIONS_DIR = Path.home() / ".pipernet" / "sessions"
CLAUDE_PROJECTS_DIR = (
    Path.home() / ".claude" / "projects" / "-Users-blaze-Movies-Kin"
)

# Roles we extract from the JSONL.  Each maps to a sender handle suffix.
ROLE_MAP = {
    "user": "grace",       # Blaze
    "human": "grace",
    "assistant": "rocky",  # Kin / Rocky
}


# ---------------------------------------------------------------------------
# Session keypair helpers
# ---------------------------------------------------------------------------

def session_handle(session_id: str) -> str:
    """Derive the handle used to sign this session's envelopes."""
    return f"council-{session_id[:8]}"


def ensure_session_keypair(session_id: str) -> tuple[str, str, Ed25519PrivateKey]:
    """
    Load or create an Ed25519 keypair for this session.

    Returns (handle, pubkey_hex, private_key).
    The private key is stored at ~/.pipernet/sessions/<session-id>.bin.
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    handle = session_handle(session_id)
    key_path = SESSIONS_DIR / f"{session_id}.bin"

    if key_path.exists():
        # Reuse existing keypair so re-runs produce consistent handles.
        raw = key_path.read_bytes()
        sk = Ed25519PrivateKey.from_private_bytes(raw)
        pk_hex = sk.public_key().public_bytes_raw().hex()
        # Re-register in case pubkeys.json was wiped.
        register_pubkey(handle, pk_hex)
        return handle, pk_hex, sk

    # Generate fresh keypair, save raw private bytes.
    sk = Ed25519PrivateKey.generate()
    raw = sk.private_bytes_raw()
    key_path.write_bytes(raw)
    os.chmod(key_path, 0o600)

    pk_hex = sk.public_key().public_bytes_raw().hex()
    register_pubkey(handle, pk_hex)
    return handle, pk_hex, sk


# ---------------------------------------------------------------------------
# JSONL transcript parsing
# ---------------------------------------------------------------------------

def extract_messages(jsonl_path: Path) -> list[dict]:
    """
    Parse a Claude Code session JSONL and return a flat list of messages.

    Each item has:
      role       — "user" | "assistant"
      body       — the plain-text content
      timestamp  — ISO string (from the record if present, else synthetic)
    """
    messages: list[dict] = []

    raw_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # Claude Code session records have various shapes.  We care about
        # records that carry a "message" dict with role + content.
        msg = rec.get("message") or rec
        role = msg.get("role") or rec.get("role")
        if role not in ("user", "human", "assistant"):
            continue

        content = msg.get("content") or rec.get("content") or ""

        # Content can be a plain string or a list of blocks.
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
            body = "\n".join(p for p in text_parts if p).strip()
        elif isinstance(content, str):
            body = content.strip()
        else:
            body = str(content).strip()

        if not body:
            continue

        # Timestamp: prefer record field, fall back to now.
        ts = (
            rec.get("timestamp")
            or rec.get("created_at")
            or msg.get("timestamp")
            or datetime.now(timezone.utc).isoformat()
        )

        messages.append({"role": role, "body": body, "timestamp": ts})

    return messages


# ---------------------------------------------------------------------------
# Find most-recent session file
# ---------------------------------------------------------------------------

def latest_session_jsonl() -> Path | None:
    """Return the most-recently-modified *.jsonl in the Claude projects dir."""
    if not CLAUDE_PROJECTS_DIR.exists():
        return None
    candidates = list(CLAUDE_PROJECTS_DIR.glob("*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    if argv:
        session_path = Path(argv[0]).expanduser().resolve()
        if not session_path.exists():
            print(f"ERROR: file not found: {session_path}", file=sys.stderr)
            return 1
    else:
        session_path = latest_session_jsonl()
        if session_path is None:
            print(
                f"ERROR: no *.jsonl found under {CLAUDE_PROJECTS_DIR}",
                file=sys.stderr,
            )
            return 1
        print(f"Auto-selected: {session_path.name}")

    # Derive a session ID from the filename stem.
    session_id = session_path.stem  # e.g. "fe04b6bc-a5d3-4ede-a87e-956bda608329"
    channel_name = f"council-{session_id[:8]}"

    # Ensure keypair.
    handle, pubkey_hex, sk = ensure_session_keypair(session_id)

    # Parse messages.
    messages = extract_messages(session_path)
    if not messages:
        print("WARNING: no user/assistant messages found in session.", file=sys.stderr)
        return 0

    # Build and append envelopes.
    sequence = next_sequence(handle, channel_name)
    parent: list | None = None
    written = 0

    for msg in messages:
        role_handle = f"{handle}-{ROLE_MAP.get(msg['role'], msg['role'])}"
        # Re-register the per-role handle alias so verify works.
        register_pubkey(role_handle, pubkey_hex)

        # Build the unsigned envelope with the original session timestamp,
        # then sign it — so the signature covers the real timestamp, not now.
        unsigned = {
            "from": role_handle,
            "sequence": sequence,
            "parent": parent,
            "modes": ["txt"],
            "body": [["txt", msg["body"]]],
            "timestamp": msg["timestamp"],
        }
        env = sign_envelope(unsigned, sk)

        append_to_channel(channel_name, env)

        # Each message is parent of the next (linear chain).
        parent = [sequence, role_handle]
        sequence += 1
        written += 1

    channel_file = pipernet_home() / "channels" / f"{channel_name}.jsonl"

    print(f"Processed  {len(messages)} messages  →  {written} envelopes")
    print(f"Channel    {channel_file}")
    print(f"Handle     {handle}   pubkey={pubkey_hex}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
