"""
pipernet-mcp — MCP server for Pipernet signing primitives + Oracle gateway
=========================================================================
Exposes Pipernet's identity/signing/channel primitives and a privacy-firewalled
gateway to the Oracle knowledge graph via Anthropic's Model Context Protocol.

Transport: Streamable HTTP (MCP spec §Transport/Streamable-HTTP)
Endpoint:  http://localhost:9000/mcp  (configurable via --port)

Usage:
    python -m mcp_server.main [--port 9000]

Connect from Claude Desktop:
    Add to ~/Library/Application Support/Claude/claude_desktop_config.json:
    {
      "mcpServers": {
        "pipernet": {
          "type": "http",
          "url": "http://localhost:9000/mcp"
        }
      }
    }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import logging
from pathlib import Path
from typing import Any

import aiohttp
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Add the parent package (pipernet CLI core) to sys.path so we can reuse it.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cli.core import (
    build_envelope,
    verify_envelope,
    append_to_channel,
    read_channel,
    next_sequence,
    load_pubkey_registry,
    register_pubkey,
    keystore_path,
    generate_keypair,
    load_private_key,
    canonical,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ORACLE_BASE = os.environ.get("ORACLE_URL", "https://oracle.axxis.world")
ORACLE_TOKEN = os.environ.get(
    "ORACLE_TOKEN",
    "sHSqxUNa4PHn4FtEFXgypZmforoRwFYSZgEmFimzJtLQzY5HIH85JL7g-8Wa3H4j",
)

SERVER_NAME = "pipernet-mcp"
SERVER_VERSION = "0.1.0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pipernet.mcp")

# ---------------------------------------------------------------------------
# Server configuration (may be overridden by CLI args before mcp is created)
# ---------------------------------------------------------------------------
_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 9000

# ---------------------------------------------------------------------------
# Privacy firewall
# ---------------------------------------------------------------------------

# Fields stripped from every Oracle response item regardless of position.
_STRIP_FIELDS_EXACT = {
    "_id", "_neo4j_id", "token", "secret", "password",
    # pubkeys are not exposed via Oracle gateway — callers should use pipernet_register_peer
    "pubkey_hex", "pubkey",
}

# Field suffix patterns that get stripped.
_STRIP_SUFFIXES = ("_token", "_key", "_pubkey", "_secret", "_password")

# Prefixes for field names that get stripped.
_STRIP_PREFIXES = ("internal_", "private_", "session_", "auth_")

# If an item's `tags` list contains any of these, the entire item is dropped.
_DROP_TAG_VALUES = {"private", "internal", "sensitive", "staging-only", "do-not-share"}

_CONTENT_MAX_CHARS = 500


def _should_strip_field(key: str) -> bool:
    if key in _STRIP_FIELDS_EXACT:
        return True
    k = key.lower()
    if any(k.endswith(s) for s in _STRIP_SUFFIXES):
        return True
    if any(k.startswith(p) for p in _STRIP_PREFIXES):
        return True
    return False


def _filter_item(item: dict) -> dict | None:
    """Apply privacy firewall to a single Oracle result item.

    Returns None if the item should be dropped entirely.
    Returns a cleaned dict otherwise.
    """
    # Drop items with forbidden tags
    tags = item.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    if _DROP_TAG_VALUES.intersection(set(str(t).lower() for t in tags)):
        return None

    # Build filtered dict
    out: dict = {}
    for k, v in item.items():
        if _should_strip_field(k):
            continue
        # Truncate content field
        if k == "content" and isinstance(v, str) and len(v) > _CONTENT_MAX_CHARS:
            v = v[:_CONTENT_MAX_CHARS] + "…"
        out[k] = v

    return out


def _filter_oracle_response(raw: Any) -> list[dict]:
    """Normalise Oracle response shape and apply privacy filter.

    Oracle returns either:
      - {"results": [...]}
      - {"items": [...]}
      - [...]
      - {"error": "..."}
    """
    if isinstance(raw, dict):
        if "error" in raw:
            return []
        items = raw.get("results") or raw.get("items") or raw.get("observations") or []
        if not items and isinstance(raw, dict):
            # Might be a single item
            items = [raw] if raw.get("content") else []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    filtered = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result = _filter_item(item)
        if result is not None:
            filtered.append(result)
    return filtered


# ---------------------------------------------------------------------------
# Oracle HTTP helper
# ---------------------------------------------------------------------------

_ORACLE_OFFLINE = {
    "error": "oracle gateway offline; pipernet primitives still available",
    "oracle_status": "offline",
}


async def _oracle_get(path: str, params: dict | None = None) -> Any:
    """GET from Oracle V4. Returns parsed JSON or _ORACLE_OFFLINE dict on any error."""
    url = f"{ORACLE_BASE}{path}"
    headers = {"Authorization": f"Bearer {ORACLE_TOKEN}"}
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, params=params) as resp:
                text = await resp.text()
                if resp.status in (401, 403):
                    log.warning("Oracle returned %d — token likely rotated", resp.status)
                    return _ORACLE_OFFLINE
                if resp.status != 200:
                    log.warning("Oracle GET %s → %d", path, resp.status)
                    return _ORACLE_OFFLINE
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    log.error("Oracle returned non-JSON: %s", text[:200])
                    return _ORACLE_OFFLINE
    except Exception as exc:
        log.error("Oracle request failed: %s", exc)
        return _ORACLE_OFFLINE


async def _oracle_post(path: str, payload: dict) -> Any:
    """POST to Oracle V4. Returns parsed JSON or _ORACLE_OFFLINE dict on any error."""
    url = f"{ORACLE_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {ORACLE_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                text = await resp.text()
                if resp.status in (401, 403):
                    log.warning("Oracle returned %d — token likely rotated", resp.status)
                    return _ORACLE_OFFLINE
                if resp.status != 200:
                    log.warning("Oracle POST %s → %d", path, resp.status)
                    return _ORACLE_OFFLINE
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    log.error("Oracle returned non-JSON: %s", text[:200])
                    return _ORACLE_OFFLINE
    except Exception as exc:
        log.error("Oracle request failed: %s", exc)
        return _ORACLE_OFFLINE


# ---------------------------------------------------------------------------
# FastMCP server — host/port are defaults here; main() mutates mcp.settings
# before calling mcp.run() so the user's --host/--port values take effect.
# ---------------------------------------------------------------------------

mcp = FastMCP(
    SERVER_NAME,
    host=_DEFAULT_HOST,
    port=_DEFAULT_PORT,
    streamable_http_path="/mcp",
    instructions=(
        "Pipernet MCP — sign and verify Pipernet envelopes, read/write local channel logs, "
        "and query the Oracle knowledge graph through a privacy firewall.\n\n"
        "All Oracle results are filtered: private/internal/sensitive items are dropped, "
        "content is truncated to 500 chars, and internal metadata fields are stripped.\n\n"
        "Pipernet primitives use local keystores at ~/.pipernet/ (or $PIPERNET_HOME/)."
    ),
)


# ===========================================================================
# TOOL 1: pipernet_send
# ===========================================================================

@mcp.tool()
async def pipernet_send(
    handle: str,
    channel: str,
    body: str,
    parent: str | None = None,
) -> dict:
    """Build and sign a Pipernet envelope for `handle` on `channel`.

    Signs using the Ed25519 private key in the local keystore (~/.pipernet/<handle>.private.bin).
    Appends the signed envelope to the local channel JSONL log.

    Args:
        handle: Pipernet identity handle. Must have a local keystore (run pipernet keygen first).
        channel: Channel name (e.g. "room", "general", "pipernet-dev").
        body: Text content of the message.
        parent: Optional JSON string for threading, e.g. '[3, "alice"]' (sequence, sender).

    Returns:
        The signed envelope as a dict.
    """
    parent_val: list | None = None
    if parent:
        try:
            parent_val = json.loads(parent)
        except json.JSONDecodeError:
            return {"error": f"parent must be valid JSON, got: {parent!r}"}

    ks = keystore_path(handle)
    if not ks.exists():
        return {
            "error": f"no keystore for handle '{handle}'. "
                     f"Create one with: pipernet keygen --handle {handle}",
            "keystore_path": str(ks),
        }

    seq = next_sequence(handle, channel)
    try:
        envelope = build_envelope(
            sender=handle,
            sequence=seq,
            body=body,
            parent=parent_val,
        )
    except Exception as exc:
        return {"error": f"envelope build failed: {type(exc).__name__}: {exc}"}

    append_to_channel(channel, envelope)
    log.info("pipernet_send: handle=%s channel=%s seq=%d", handle, channel, seq)
    return envelope


# ===========================================================================
# TOOL 2: pipernet_inbox
# ===========================================================================

@mcp.tool()
async def pipernet_inbox(channel: str, limit: int = 20) -> dict:
    """Read recent envelopes from a local Pipernet channel log.

    Args:
        channel: Channel name (e.g. "room").
        limit: Maximum number of envelopes to return (newest last). Default 20.

    Returns:
        Dict with `channel`, `count`, `envelopes` list.
    """
    envelopes = read_channel(channel)
    # Return the last `limit` entries
    sliced = envelopes[-limit:] if len(envelopes) > limit else envelopes
    return {
        "channel": channel,
        "count": len(sliced),
        "total_in_log": len(envelopes),
        "envelopes": sliced,
    }


# ===========================================================================
# TOOL 3: pipernet_verify
# ===========================================================================

@mcp.tool()
async def pipernet_verify(envelope_json: str) -> dict:
    """Verify the Ed25519 signature on a Pipernet envelope.

    Checks the signature against the pubkey registered for the `from` handle
    in the local pubkey registry (~/.pipernet/pubkeys.json).

    Args:
        envelope_json: JSON string of the envelope to verify.

    Returns:
        {"valid": bool, "from": str, "reason": str | None, "details": {...}}
    """
    try:
        envelope = json.loads(envelope_json)
    except json.JSONDecodeError as e:
        return {"valid": False, "from": None, "reason": f"invalid JSON: {e}"}

    return verify_envelope(envelope)


# ===========================================================================
# TOOL 4: pipernet_register_peer
# ===========================================================================

@mcp.tool()
async def pipernet_register_peer(handle: str, pubkey_hex: str) -> dict:
    """Register a peer's Ed25519 public key for envelope verification.

    Saves to the local pubkey registry (~/.pipernet/pubkeys.json).
    Required before pipernet_verify can check envelopes from that handle.

    Args:
        handle: The peer's Pipernet handle.
        pubkey_hex: Their 64-char hex-encoded Ed25519 public key.

    Returns:
        {"ok": true, "handle": str, "pubkey_hex": str}
    """
    if len(pubkey_hex) != 64:
        return {
            "error": f"pubkey_hex must be 64 hex chars (32 bytes), got {len(pubkey_hex)} chars"
        }
    try:
        bytes.fromhex(pubkey_hex)
    except ValueError:
        return {"error": "pubkey_hex is not valid hexadecimal"}

    register_pubkey(handle, pubkey_hex)
    log.info("pipernet_register_peer: handle=%s", handle)
    return {"ok": True, "handle": handle, "pubkey_hex": pubkey_hex}


# ===========================================================================
# TOOL 5: pipernet_whoami
# ===========================================================================

@mcp.tool()
async def pipernet_whoami(handle: str) -> dict:
    """Show identity information for a Pipernet handle.

    Args:
        handle: Pipernet handle to look up.

    Returns:
        Dict with handle, tier, has_keystore, pubkey_hex (if registered).
    """
    reg = load_pubkey_registry()
    pk = reg.get(handle)
    has_keystore = keystore_path(handle).exists()

    if not pk and not has_keystore:
        return {
            "handle": handle,
            "registered": False,
            "has_keystore": False,
            "hint": f"Run `pipernet keygen --handle {handle}` to create an identity.",
        }

    return {
        "handle": handle,
        "registered": pk is not None,
        "has_keystore": has_keystore,
        "tier": "0" if has_keystore else "external (no local private key)",
        # Intentionally NOT exposing pubkey_hex via this tool per privacy model.
        # Pubkeys are shared out-of-band (via pipernet_register_peer on the recipient side).
        "note": "pubkey not exposed via MCP gateway; share via direct channel handshake",
    }


# ===========================================================================
# TOOL 6: oracle_search
# ===========================================================================

@mcp.tool()
async def oracle_search(query: str, limit: int = 10) -> dict:
    """Search the Oracle knowledge graph for observations matching `query`.

    Results pass through a privacy firewall:
    - Items tagged private/internal/sensitive/staging-only are dropped
    - Internal metadata fields (_id, _neo4j_id, *_token, *_key, etc.) are stripped
    - Content is truncated to 500 characters

    Args:
        query: Natural language search query.
        limit: Maximum results to return (1–50). Default 10.

    Returns:
        {"results": [...], "count": int, "oracle_status": "ok" | "offline"}
    """
    limit = max(1, min(50, limit))
    raw = await _oracle_post("/query", {"query": query, "limit": limit})

    if raw == _ORACLE_OFFLINE or (isinstance(raw, dict) and "oracle_status" in raw):
        return {**_ORACLE_OFFLINE, "results": []}

    filtered = _filter_oracle_response(raw)
    return {"results": filtered, "count": len(filtered), "oracle_status": "ok"}


# ===========================================================================
# TOOL 7: oracle_recent
# ===========================================================================

@mcp.tool()
async def oracle_recent(limit: int = 10) -> dict:
    """Return the most recently committed observations from Oracle.

    Results pass through the same privacy firewall as oracle_search.

    Args:
        limit: Maximum results to return (1–50). Default 10.

    Returns:
        {"results": [...], "count": int, "oracle_status": "ok" | "offline"}
    """
    limit = max(1, min(50, limit))
    raw = await _oracle_get("/recent", params={"limit": str(limit)})

    if raw == _ORACLE_OFFLINE or (isinstance(raw, dict) and "oracle_status" in raw):
        return {**_ORACLE_OFFLINE, "results": []}

    filtered = _filter_oracle_response(raw)
    return {"results": filtered, "count": len(filtered), "oracle_status": "ok"}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m mcp_server.main",
        description=(
            "Pipernet MCP server — Streamable HTTP transport.\n"
            "Exposes Pipernet signing primitives + Oracle knowledge gateway.\n\n"
            "Connect from Claude Desktop:\n"
            "  Add to claude_desktop_config.json mcpServers:\n"
            "  { \"pipernet\": { \"type\": \"http\", \"url\": \"http://localhost:9000/mcp\" } }"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--port", type=int, default=9000,
        help="Port to listen on (default: 9000)",
    )
    p.add_argument(
        "--host", default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0; use 127.0.0.1 for localhost-only)",
    )
    p.add_argument(
        "--oracle-url", default=ORACLE_BASE,
        help=f"Oracle V4 base URL (default: {ORACLE_BASE})",
    )
    p.add_argument(
        "--oracle-token", default=None,
        help="Oracle bearer token (default: $ORACLE_TOKEN env var or compiled-in value)",
    )
    return p


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    # Apply overrides
    global ORACLE_BASE, ORACLE_TOKEN
    ORACLE_BASE = args.oracle_url
    if args.oracle_token:
        ORACLE_TOKEN = args.oracle_token

    # Update the module-level mcp instance's host/port settings.
    # FastMCP reads these from self.settings at serve time.
    mcp.settings.host = args.host
    mcp.settings.port = args.port

    print(f"""
╔══════════════════════════════════════════════════════════╗
║          pipernet-mcp  v{SERVER_VERSION}                          ║
╚══════════════════════════════════════════════════════════╝

Tools available:
  pipernet_send            Build + sign an envelope
  pipernet_inbox           Read channel JSONL log
  pipernet_verify          Verify envelope signature
  pipernet_register_peer   Add peer pubkey to registry
  pipernet_whoami          Show handle identity
  oracle_search            Search Oracle knowledge graph
  oracle_recent            Recent Oracle observations

Transport:    Streamable HTTP
MCP endpoint: http://{args.host}:{args.port}/mcp
Manifest:     http://{args.host}:{args.port}/

Oracle URL:   {ORACLE_BASE}
Pipernet home: {os.environ.get('PIPERNET_HOME', '~/.pipernet')}

Claude Desktop config:
  {{
    "mcpServers": {{
      "pipernet": {{
        "type": "http",
        "url": "http://localhost:{args.port}/mcp"
      }}
    }}
  }}
""")

    # Build a Starlette wrapper that adds a manifest route at GET /
    # FastMCP's streamable_http_app() is itself a full Starlette app that owns
    # the route at /mcp — we wrap it with a simple middleware that intercepts
    # GET / before forwarding to the MCP app.
    mcp_starlette = mcp.streamable_http_app()

    manifest_body = "\n".join([
        f"{SERVER_NAME}  v{SERVER_VERSION}",
        "Pipernet MCP server — signing primitives + Oracle knowledge gateway",
        "",
        f"MCP endpoint:  http://{args.host}:{args.port}/mcp",
        "Transport:     Streamable HTTP (RFC 8441)",
        "",
        "Tools:",
        "  pipernet_send            — Build + sign an envelope",
        "  pipernet_inbox           — Read channel JSONL log",
        "  pipernet_verify          — Verify envelope signature",
        "  pipernet_register_peer   — Register peer pubkey",
        "  pipernet_whoami          — Show handle identity",
        "  oracle_search            — Search Oracle knowledge graph (privacy-firewalled)",
        "  oracle_recent            — Recent Oracle observations (privacy-firewalled)",
        "",
        "Connect from Claude Desktop:",
        "  Add to ~/Library/Application Support/Claude/claude_desktop_config.json:",
        "  {",
        '    "mcpServers": {',
        '      "pipernet": {',
        '        "type": "http",',
        f'        "url": "http://localhost:{args.port}/mcp"',
        "      }",
        "    }",
        "  }",
        "",
        f"Connect from any MCP-compatible client: http://{args.host}:{args.port}/mcp",
        "",
        "Privacy firewall: Oracle results strip _id, *_token, *_key, session_*, auth_*",
        "  Items tagged private/internal/sensitive/staging-only are dropped.",
        "  Content truncated to 500 chars.",
        "",
        f"Oracle status: {'gateway configured' if ORACLE_TOKEN else 'no token'}",
    ])

    class ManifestMiddleware:
        """ASGI middleware: intercepts GET / for manifest; everything else → MCP app."""

        def __init__(self, mcp_app: Any) -> None:
            self._mcp = mcp_app

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http" and scope["method"] == "GET" and scope["path"] in ("/", ""):
                body = manifest_body.encode("utf-8")
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
            else:
                await self._mcp(scope, receive, send)

    app = ManifestMiddleware(mcp_starlette)

    import uvicorn
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    import anyio
    anyio.run(server.serve)


if __name__ == "__main__":
    main()
