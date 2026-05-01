"""
pipernet relay server — cli/server.py

Run with:
    pipernet serve --port 8000 --host 0.0.0.0

Endpoints:
    POST /channels/<name>           — submit a signed envelope
    GET  /channels/<name>           — read full channel (JSON array)
    GET  /channels/<name>?format=jsonl — raw JSONL
    GET  /channels/<name>/events    — SSE stream of new envelopes
    GET  /pubkeys                   — pubkey registry
    POST /pubkeys                   — register a peer pubkey
    POST /gossip                    — relay-to-relay envelope batch
    GET  /health                    — node health
    GET  /                          — quickstart help

No auth in v0. Signatures are the auth.
CORS: Access-Control-Allow-Origin: * on all GETs.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

try:
    from aiohttp import web
except ImportError:
    raise ImportError(
        "aiohttp is required for `pipernet serve`. "
        "Install it: pip install aiohttp>=3.9"
    )

from . import core

VERSION = "0.2.0"


# ---------------------------------------------------------------------------
# SSE subscriber registry
# ---------------------------------------------------------------------------

# channel_name -> list of asyncio.Queue
_subscribers: dict[str, list[asyncio.Queue]] = {}


def _get_subs(channel: str) -> list[asyncio.Queue]:
    return _subscribers.setdefault(channel, [])


async def _broadcast(channel: str, envelope: dict) -> None:
    """Push envelope to all SSE subscribers for a channel."""
    payload = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    dead: list[asyncio.Queue] = []
    for q in _get_subs(channel):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        try:
            _get_subs(channel).remove(q)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# CORS helper
# ---------------------------------------------------------------------------

def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def _json_response(data: Any, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, indent=2),
        status=status,
        content_type="application/json",
        headers=_cors_headers(),
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_root(request: web.Request) -> web.Response:
    node_handle = request.app["node_handle"]
    text = f"""pipernet relay v{VERSION}  node: {node_handle}

ENDPOINTS
---------
POST /channels/<name>              Submit a signed envelope (JSON body)
GET  /channels/<name>              Read channel (JSON array)
GET  /channels/<name>?format=jsonl Read channel (raw JSONL)
GET  /channels/<name>/events       SSE stream — subscribe to new envelopes
GET  /pubkeys                      Pubkey registry (all registered peers)
POST /pubkeys                      Register a peer: {{handle, pubkey_hex, identity_assertion}}
POST /gossip                       Relay-to-relay: submit a batch of signed envelopes
GET  /health                       Node health + stats

CURL QUICKSTART (alice → relay → bob)
--------------------------------------
# alice generates a keypair and sends to relay:
pipernet keygen --handle alice
pipernet send --handle alice --channel test --body "hello relay" | \\
  curl -s -X POST http://localhost:8000/channels/test \\
       -H 'Content-Type: application/json' -d @-

# bob reads the channel:
curl http://localhost:8000/channels/test

# bob subscribes to live events (SSE):
curl -N http://localhost:8000/channels/test/events

# alice posts another message — bob sees it arrive instantly.
"""
    return web.Response(text=text, content_type="text/plain", headers=_cors_headers())


async def handle_post_channel(request: web.Request) -> web.Response:
    """Receive a signed envelope, validate, append, broadcast."""
    channel = request.match_info["name"]
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        return _json_response({"error": f"invalid JSON: {e}"}, 400)

    if not isinstance(body, dict):
        return _json_response({"error": "envelope must be a JSON object"}, 400)

    # Load registry — relay has all registered pubkeys
    registry = core.load_pubkey_registry()
    verdict = core.verify_envelope(body, registry=registry)
    if not verdict["valid"]:
        return _json_response(
            {"error": "signature does not verify", "reason": verdict["reason"]},
            400,
        )

    # Append to channel
    core.append_to_channel(channel, body)

    # Broadcast to SSE subscribers
    await _broadcast(channel, body)

    return _json_response(body, 200)


async def handle_get_channel(request: web.Request) -> web.Response:
    """Return all envelopes in a channel."""
    channel = request.match_info["name"]
    fmt = request.rel_url.query.get("format", "json")
    envelopes = core.read_channel(channel)

    if fmt == "jsonl":
        lines = "\n".join(
            json.dumps(e, sort_keys=True, separators=(",", ":")) for e in envelopes
        )
        return web.Response(
            text=lines,
            content_type="application/x-ndjson",
            headers={**_cors_headers()},
        )

    return _json_response(envelopes)


async def handle_sse_channel(request: web.Request) -> web.StreamResponse:
    """Server-Sent Events stream for a channel."""
    channel = request.match_info["name"]

    response = web.StreamResponse()
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    response.headers.update(_cors_headers())
    await response.prepare(request)

    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _get_subs(channel).append(queue)

    # Send connected event
    await response.write(
        f"event: connected\ndata: {{\"channel\":\"{channel}\"}}\n\n".encode()
    )

    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                await response.write(
                    f"event: envelope\ndata: {payload}\n\n".encode()
                )
            except asyncio.TimeoutError:
                # Heartbeat to keep connection alive
                await response.write(b"event: heartbeat\ndata: {}\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        try:
            _get_subs(channel).remove(queue)
        except ValueError:
            pass

    return response


async def handle_get_pubkeys(request: web.Request) -> web.Response:
    """Return the local pubkey registry."""
    registry = core.load_pubkey_registry()
    return _json_response(registry)


async def handle_post_pubkeys(request: web.Request) -> web.Response:
    """Register a peer pubkey.

    Body: {handle, pubkey_hex, identity_assertion}
    identity_assertion is the dict returned by `pipernet keygen`.
    We verify the self_signature_hex to confirm handle owns the key.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        return _json_response({"error": f"invalid JSON: {e}"}, 400)

    handle = body.get("handle")
    pubkey_hex = body.get("pubkey_hex")
    identity_assertion = body.get("identity_assertion")

    if not handle:
        return _json_response({"error": "missing 'handle'"}, 400)
    if not pubkey_hex:
        return _json_response({"error": "missing 'pubkey_hex'"}, 400)

    # Validate hex
    if len(pubkey_hex) != 64:
        return _json_response({"error": "pubkey_hex must be 64 hex chars"}, 400)
    try:
        bytes.fromhex(pubkey_hex)
    except ValueError:
        return _json_response({"error": "pubkey_hex is not valid hex"}, 400)

    # If identity_assertion provided, verify self-signature
    if identity_assertion:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            import base64

            ia = dict(identity_assertion)
            sig_hex = ia.pop("self_signature_hex", None)
            if not sig_hex:
                return _json_response(
                    {"error": "identity_assertion missing self_signature_hex"}, 400
                )
            pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
            pk.verify(bytes.fromhex(sig_hex), core.canonical(ia))
        except Exception as e:
            return _json_response(
                {"error": f"identity assertion signature does not verify: {e}"}, 400
            )

    core.register_pubkey(handle, pubkey_hex)
    return _json_response({"registered": handle, "pubkey_hex": pubkey_hex})


async def handle_gossip(request: web.Request) -> web.Response:
    """Accept a batch of envelopes from a peer relay.

    Body: list of signed envelopes.
    Validates each, deduplicates by signature, appends new ones.
    Returns {appended: N, rejected: [...]}
    """
    try:
        batch = await request.json()
    except json.JSONDecodeError as e:
        return _json_response({"error": f"invalid JSON: {e}"}, 400)

    if not isinstance(batch, list):
        return _json_response({"error": "body must be a JSON array of envelopes"}, 400)

    registry = core.load_pubkey_registry()
    appended = 0
    rejected = []

    for envelope in batch:
        if not isinstance(envelope, dict):
            rejected.append({"error": "not an object", "item": str(envelope)[:80]})
            continue

        channel = envelope.get("channel", "gossip")
        sig = envelope.get("signature")

        # Deduplication: check if this signature already exists in channel
        existing = core.read_channel(channel)
        if any(e.get("signature") == sig for e in existing):
            continue  # already have it, skip silently

        verdict = core.verify_envelope(envelope, registry=registry)
        if not verdict["valid"]:
            rejected.append({"reason": verdict["reason"], "from": envelope.get("from")})
            continue

        core.append_to_channel(channel, envelope)
        await _broadcast(channel, envelope)
        appended += 1

    return _json_response({"appended": appended, "rejected": rejected})


async def handle_health(request: web.Request) -> web.Response:
    """Return node health stats."""
    start_time = request.app["start_time"]
    node_handle = request.app["node_handle"]
    uptime = int(time.time() - start_time)

    # Count channels
    channels_dir = core.home() / "channels"
    channel_count = len(list(channels_dir.glob("*.jsonl"))) if channels_dir.exists() else 0

    # Count peers
    registry = core.load_pubkey_registry()
    peer_count = len(registry)

    # Count live SSE subscribers
    sse_count = sum(len(v) for v in _subscribers.values())

    return _json_response({
        "status": "ok",
        "node": node_handle,
        "version": VERSION,
        "uptime_seconds": uptime,
        "channel_count": channel_count,
        "peer_count": peer_count,
        "sse_subscribers": sse_count,
    })


# ---------------------------------------------------------------------------
# OPTIONS preflight (CORS)
# ---------------------------------------------------------------------------

async def handle_options(request: web.Request) -> web.Response:
    return web.Response(status=204, headers=_cors_headers())


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def build_app(node_handle: str | None = None) -> web.Application:
    """Build and return the aiohttp Application."""
    if node_handle is None:
        # Try to infer from first registered pubkey
        reg = core.load_pubkey_registry()
        node_handle = next(iter(reg), "anonymous")

    app = web.Application()
    app["start_time"] = time.time()
    app["node_handle"] = node_handle

    app.router.add_route("OPTIONS", "/{path_info:.*}", handle_options)
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/pubkeys", handle_get_pubkeys)
    app.router.add_post("/pubkeys", handle_post_pubkeys)
    app.router.add_post("/gossip", handle_gossip)

    # Channel routes — order matters: /events before generic GET
    app.router.add_get("/channels/{name}/events", handle_sse_channel)
    app.router.add_get("/channels/{name}", handle_get_channel)
    app.router.add_post("/channels/{name}", handle_post_channel)

    return app


def run_server(host: str = "0.0.0.0", port: int = 8000, node_handle: str | None = None) -> None:
    """Start the relay server. Blocks until Ctrl+C."""
    app = build_app(node_handle=node_handle)
    print(f"pipernet relay v{VERSION}")
    print(f"node:    {app['node_handle']}")
    print(f"storage: {core.home()}")
    print(f"listen:  http://{host}:{port}")
    print(f"")
    print(f"Endpoints:")
    print(f"  POST http://{host}:{port}/channels/<name>        submit envelope")
    print(f"  GET  http://{host}:{port}/channels/<name>        read channel")
    print(f"  GET  http://{host}:{port}/channels/<name>/events SSE stream")
    print(f"  GET  http://{host}:{port}/pubkeys                peer registry")
    print(f"  POST http://{host}:{port}/pubkeys                register peer")
    print(f"  POST http://{host}:{port}/gossip                 relay sync")
    print(f"  GET  http://{host}:{port}/health                 health check")
    print(f"")
    print(f"Press Ctrl+C to stop.")
    web.run_app(app, host=host, port=port, print=None)
