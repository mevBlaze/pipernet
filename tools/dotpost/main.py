"""
pipernet dotpost — mesh communication via Oracle.

Posts a message to the Oracle knowledge graph as a DOT envelope,
tagged for routing. Any other mesh node can query Oracle and read
their incoming dotposts. No paste relay. No audio bridge. The brain
is the bus.

Usage:
    pipernet dotpost send --to <handle> --body "<text>"   [--from <handle>]
    pipernet dotpost recv [--for <handle>] [--since <iso>] [--limit 20]
    pipernet dotpost watch [--for <handle>] [--interval 30]

Each dotpost becomes a typed Oracle observation with:
    type    = "dotpost"
    tags    = ["dotpost", "from:<sender>", "to:<recipient>"]
    content = the message body
    source  = "pipernet-mesh-<sender>"

Other nodes — Loom, Janus, Jared, anyone — query Oracle for their tag
and find their messages. The mesh routes through the brain.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


ORACLE_BASE = os.getenv("ORACLE_BASE", "https://oracle.axxis.world")


def _oracle_token() -> str:
    """Get the Oracle V4 bearer token, in priority order."""
    if t := os.getenv("ORACLE_AUTH_TOKEN"):
        return t
    if t := os.getenv("TREE_AUTH_TOKEN"):
        return t
    # Fall back to oracle_v3/.env — try a few likely paths
    candidates = [
        Path(__file__).resolve().parent.parent.parent.parent / "oracle_v3" / ".env",
        Path("/Users/blaze/Movies/Kin/oracle_v3/.env"),
        Path.home() / "Movies" / "Kin" / "oracle_v3" / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ORACLE_AUTH_TOKEN="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError(
        "Oracle token not found. Set ORACLE_AUTH_TOKEN env var or add it to oracle_v3/.env"
    )


def _mcp_call(method: str, params: dict) -> dict:
    """Make an MCP JSON-RPC call against Oracle V4."""
    payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000), "method": method, "params": params}
    req = Request(
        f"{ORACLE_BASE}/mcp/",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_oracle_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    # MCP responses are SSE-style: lines beginning "data: <json>"
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise RuntimeError(f"unexpected MCP response: {body[:300]}")


def _tool_call(tool_name: str, args: dict) -> dict:
    resp = _mcp_call("tools/call", {"name": tool_name, "arguments": args})
    if "error" in resp:
        raise RuntimeError(f"oracle error: {resp['error']}")
    content = resp.get("result", {}).get("content", [])
    if not content:
        return {}
    text = content[0].get("text", "")
    # Some tools return JSON-string text, some return prose. Try parsing.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


def cmd_send(args: argparse.Namespace) -> int:
    sender = args.from_handle or os.getenv("PIPERNET_HANDLE", "rocky")
    recipient = args.to
    body = args.body

    payload = {
        "source": f"pipernet-mesh-{sender}",
        "extracted": {
            "items": [
                {
                    "content": body,
                    "type": "dotpost",
                    "rationale": f"DOTpost from {sender} to {recipient} via mesh",
                    "tags": [
                        "dotpost",
                        f"from:{sender}",
                        f"to:{recipient}",
                        "mesh",
                    ],
                    "confidence": 0.95,
                }
            ]
        },
    }
    print(f"→ dotpost {sender}@mesh → {recipient} ({len(body)} chars)", file=sys.stderr)
    result = _tool_call("oracle_ingest", payload)
    print(json.dumps(result, indent=2))
    return 0


def cmd_recv(args: argparse.Namespace) -> int:
    me = args.for_handle or os.getenv("PIPERNET_HANDLE", "rocky")
    query = f"dotpost to:{me}"
    print(f"→ querying Oracle for incoming dotposts to '{me}'", file=sys.stderr)
    result = _tool_call(
        "oracle_query",
        {"query": query, "type_filter": "dotpost"},
    )
    # Oracle query result formats vary. Print the raw text for inspection;
    # downstream tooling can parse if needed.
    if "text" in result:
        print(result["text"])
    else:
        print(json.dumps(result, indent=2))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    me = args.for_handle or os.getenv("PIPERNET_HANDLE", "rocky")
    interval = max(10, int(args.interval))
    print(
        f"→ watching for dotposts to '{me}' every {interval}s. Ctrl-C to stop.",
        file=sys.stderr,
    )
    seen: set[str] = set()
    while True:
        try:
            result = _tool_call(
                "oracle_query",
                {"query": f"dotpost to:{me}", "type_filter": "dotpost"},
            )
            text = result.get("text", "")
            # Cheap dedup: hash the text payload, only print if new
            digest = str(hash(text))
            if digest not in seen:
                seen.add(digest)
                ts = datetime.now(timezone.utc).isoformat()
                print(f"\n[{ts}]")
                print(text)
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n→ stopped", file=sys.stderr)
            return 0
        except (URLError, HTTPError, RuntimeError) as e:
            print(f"! error: {e}; retrying in {interval}s", file=sys.stderr)
            time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="pipernet-dotpost",
        description="mesh comms via Oracle — the brain is the bus",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_send = sub.add_parser("send", help="post a message to a mesh peer via Oracle")
    p_send.add_argument("--to", required=True, help="recipient handle (e.g., loam, janus, jared)")
    p_send.add_argument("--body", required=True, help="message body")
    p_send.add_argument("--from", dest="from_handle", help="sender handle (default: $PIPERNET_HANDLE or rocky)")
    p_send.set_defaults(func=cmd_send)

    p_recv = sub.add_parser("recv", help="read incoming dotposts from Oracle")
    p_recv.add_argument("--for", dest="for_handle", help="recipient handle to read for (default: $PIPERNET_HANDLE or rocky)")
    p_recv.add_argument("--since", help="only show dotposts after this ISO timestamp")
    p_recv.add_argument("--limit", type=int, default=20)
    p_recv.set_defaults(func=cmd_recv)

    p_watch = sub.add_parser("watch", help="poll Oracle continuously for new dotposts")
    p_watch.add_argument("--for", dest="for_handle", help="recipient handle (default: $PIPERNET_HANDLE or rocky)")
    p_watch.add_argument("--interval", type=int, default=30, help="seconds between polls (min 10)")
    p_watch.set_defaults(func=cmd_watch)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
