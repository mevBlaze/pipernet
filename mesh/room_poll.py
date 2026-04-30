#!/usr/bin/env python3
"""
mesh/room_poll.py
================================================================
Read-only polling client for Oracle channel `room` (schema v1.0).

Calls the Oracle MCP server's `tools/call` JSON-RPC endpoint with
the bearer token from ~/.mcp.json. Prints any messages newer than
the last-seen sequence per source. Writes state to
./room_state.json.

Usage:
    python3 room_poll.py             # poll once, print new messages
    python3 room_poll.py --watch     # loop with 60s heartbeat
    python3 room_poll.py --since 5   # show last 5 messages even if seen

The poller does not ingest. Outbound messages from this device queue
to room_dispatch.jsonl for Loom/Jared to ingest on Rocky's behalf
until Kin-1's write scope is restored.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "room_state.json"
MCP_URL = "https://oracle.axxis.world/mcp/"
MCP_CONFIG = Path.home() / ".mcp.json"


def load_token() -> str:
    """Pull the Oracle bearer token from ~/.mcp.json."""
    cfg = json.loads(MCP_CONFIG.read_text())
    oracle = cfg["mcpServers"]["oracle"]
    return oracle["headers"]["Authorization"].removeprefix("Bearer ").strip()


def mcp_call(token: str, tool: str, args: dict) -> dict:
    """Call an MCP tool via JSON-RPC over Streamable HTTP."""
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }).encode("utf-8")

    req = urllib.request.Request(
        MCP_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
            "MCP-Protocol-Version": "2025-03-26",
            # Cloudflare WAF rejects default Python user-agent. Use a real one.
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 mesh-room-poll/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"HTTP {e.code}: {body}") from None

    # Streamable HTTP returns SSE if the server elects to stream, JSON otherwise.
    # Try plain JSON first; fall back to last-event-data.
    raw = raw.strip()
    if raw.startswith("event:") or raw.startswith("data:"):
        # Pull last `data:` line.
        data_lines = [
            line[5:].strip()
            for line in raw.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            raise RuntimeError(f"no data in SSE response: {raw[:200]}")
        return json.loads(data_lines[-1])
    return json.loads(raw)


def fetch_recent_room(token: str, limit: int = 50) -> list[str]:
    """Return the recent observation summaries from channel `room`."""
    res = mcp_call(token, "oracle_recent", {"channel": "room", "limit": limit})
    if "error" in res:
        raise RuntimeError(f"oracle_recent error: {res['error']}")
    # The oracle_recent tool returns plain text in result.content[0].text
    parts = res.get("result", {}).get("content", [])
    if not parts:
        return []
    text = parts[0].get("text", "")
    return text.splitlines()


def fetch_query(token: str, query: str, top_k: int = 8) -> str:
    res = mcp_call(token, "oracle_query", {"query": query, "top_k": top_k})
    if "error" in res:
        return f"ERROR: {res['error']}"
    parts = res.get("result", {}).get("content", [])
    return parts[0].get("text", "") if parts else ""


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_seen_lines": [], "last_poll_at": None}


def save_state(s: dict) -> None:
    STATE_FILE.write_text(json.dumps(s, indent=2))


def poll_once(token: str, *, force_show: int = 0) -> int:
    state = load_state()
    last = set(state.get("last_seen_lines", []))
    lines = fetch_recent_room(token, limit=50)
    fresh = [ln for ln in lines if ln.strip() and ln not in last]

    now = datetime.now(timezone.utc).isoformat()
    if force_show:
        to_print = lines[:force_show]
    else:
        to_print = fresh

    if to_print:
        print(f"\n=== channel `room` — {len(to_print)} message(s) "
              f"@ {now} ===")
        for ln in to_print:
            print(f"  {ln}")
    else:
        print(f"  [{now}] no new messages in channel `room`")

    # Update state with everything we just saw
    state["last_seen_lines"] = lines[:50]
    state["last_poll_at"] = now
    save_state(state)
    return len(fresh)


def main(argv: list[str]) -> int:
    token = load_token()

    # Quick sanity check — verify MCP read works
    try:
        head = fetch_query(token, "channel room schema heartbeat", top_k=2)
        print("  [auth ok] sample query returned", len(head), "chars")
    except Exception as e:
        print(f"  [auth fail] {e}", file=sys.stderr)
        return 1

    if "--watch" in argv:
        try:
            while True:
                poll_once(token)
                time.sleep(60)
        except KeyboardInterrupt:
            print("\n  watch stopped.")
            return 0

    if "--since" in argv:
        idx = argv.index("--since")
        n = int(argv[idx + 1])
        poll_once(token, force_show=n)
        return 0

    poll_once(token)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
