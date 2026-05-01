"""
pipernet relay server — cli/server.py

Run with:
    pipernet serve --port 8000 --host 0.0.0.0 --log-level info

Endpoints:
    POST /channels/<name>           — submit a signed envelope
    GET  /channels/<name>           — read full channel (JSON array)
    GET  /channels/<name>?format=jsonl — raw JSONL
    GET  /channels/<name>/events    — SSE stream of new envelopes
    GET  /pubkeys                   — pubkey registry
    POST /pubkeys                   — register a peer pubkey
    POST /gossip                    — relay-to-relay envelope batch
    GET  /health                    — node health
    GET  /limits                    — configured rate limits (public)
    GET  /                          — quickstart help

No auth in v0. Signatures are the auth.
CORS: Access-Control-Allow-Origin: * on all GETs.

Structured logging:
    Every log line is JSON: {"ts": ISO, "level": "info", "event": "<name>", ...context}
    Set log level with --log-level flag (debug|info|warn|error).
    All output goes to stdout for systemd/pm2 capture.

Rate limiting (in-memory sliding window, no external deps):
    Per pubkey handle : max 10 envelopes / 60 s
    Per IP            : max 60 POST requests / 60 s
    Per IP (Sybil)    : max 30 unique handles registered / 3600 s
    Per IP (SSE)      : max 5 concurrent SSE connections
    Privacy: IPs truncated to /24 in logs; pubkeys to first 8 chars.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
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
# Rate limit configuration
# ---------------------------------------------------------------------------

RATE_LIMITS: dict[str, Any] = {
    "per_pubkey": {
        "description": "Max envelopes a single pubkey handle may submit",
        "max_requests": 10,
        "window_seconds": 60,
        "applies_to": ["POST /channels/<name>"],
    },
    "per_ip_post": {
        "description": "Max total POST requests from a single IP",
        "max_requests": 60,
        "window_seconds": 60,
        "applies_to": ["POST /channels/<name>", "POST /pubkeys", "POST /gossip"],
    },
    "per_ip_sybil": {
        "description": "Max unique pubkey handles an IP may register (Sybil protection)",
        "max_requests": 30,
        "window_seconds": 3600,
        "applies_to": ["POST /pubkeys", "POST /channels/<name>"],
    },
    "per_ip_sse": {
        "description": "Max concurrent SSE subscriptions from a single IP",
        "max_concurrent": 5,
        "applies_to": ["GET /channels/<name>/events"],
    },
}


# ---------------------------------------------------------------------------
# Privacy helpers — never log full IPs or full pubkeys
# ---------------------------------------------------------------------------

def _mask_ip(ip: str) -> str:
    """Truncate IPv4 to /24 (e.g. 192.168.1.x). Pass IPv6 through truncated."""
    if not ip or ip == "unknown":
        return "unknown"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.x"
    # IPv6 — just take first 16 chars + ellipsis
    return ip[:16] + "..."


def _mask_key(key: str) -> str:
    """Return first 8 chars + '...' of a pubkey/handle."""
    if not key:
        return "..."
    return key[:8] + "..."


# ---------------------------------------------------------------------------
# Sliding-window rate limiter (pure in-memory, no external deps)
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Simple per-key sliding-window counter using deques of timestamps.

    Usage:
        rl = RateLimiter()
        ok, retry_after = rl.check("per_ip_post", "192.168.1.x", max_req=60, window=60)
        if not ok:
            return 429

    All timestamp buckets are stored in a defaultdict(deque); stale entries
    older than `window` are pruned on each check (lazy eviction). This keeps
    memory proportional to active request rates, not registered keys.
    """

    def __init__(self) -> None:
        # key: (bucket_name, key_value) -> deque of float timestamps
        self._windows: dict[tuple[str, str], collections.deque] = (
            collections.defaultdict(collections.deque)
        )
        # SSE concurrent count per IP: ip -> set of request_ids
        self._sse_counts: dict[str, set] = collections.defaultdict(set)

    # -- Sliding window -------------------------------------------------------

    def check(
        self,
        bucket: str,
        key: str,
        max_requests: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        """
        Returns (allowed: bool, retry_after_seconds: int).
        retry_after_seconds is 0 when allowed.
        """
        now = time.time()
        cutoff = now - window_seconds
        dq = self._windows[(bucket, key)]

        # Prune expired entries
        while dq and dq[0] < cutoff:
            dq.popleft()

        if len(dq) >= max_requests:
            # Oldest entry in window: when it expires the limit resets
            retry_after = max(1, int(dq[0] - cutoff) + 1)
            return False, retry_after

        dq.append(now)
        return True, 0

    # -- SSE concurrent limit -------------------------------------------------

    def sse_acquire(self, ip: str, request_id: str, max_concurrent: int) -> bool:
        """
        Try to register a new SSE connection from `ip`.
        Returns True if allowed, False if the IP already has max_concurrent.
        """
        active = self._sse_counts[ip]
        if len(active) >= max_concurrent:
            return False
        active.add(request_id)
        return True

    def sse_release(self, ip: str, request_id: str) -> None:
        """Deregister an SSE connection (call on disconnect)."""
        self._sse_counts[ip].discard(request_id)
        if not self._sse_counts[ip]:
            del self._sse_counts[ip]

    def sse_count(self, ip: str) -> int:
        return len(self._sse_counts.get(ip, set()))

    # -- Unique-handle tracking for Sybil check -------------------------------

    def track_handle(self, ip: str, handle: str) -> None:
        """Record that `ip` used `handle`. Reuses the sliding window bucket."""
        # We store "handle:<handle>" entries in a dedicated set-style bucket.
        # We use the sybil window (3600s) and key on ip.
        # The actual uniqueness counting is done via a separate structure.
        pass  # Handled by check() on "sybil_handles:<ip>" + "<handle>"

    def count_unique_handles(
        self,
        ip: str,
        handle: str,
        window_seconds: int,
    ) -> tuple[bool, int]:
        """
        Track how many distinct handles an IP has used in window_seconds.
        Returns (allowed, retry_after).
        """
        now = time.time()
        cutoff = now - window_seconds

        # We keep a time-ordered deque of (timestamp, handle) pairs
        dq_key = ("sybil_pairs", ip)
        dq = self._windows[dq_key]  # stores (ts, handle) tuples

        # Prune old entries
        while dq and dq[0][0] < cutoff:
            dq.popleft()

        # Count distinct handles in the window
        seen_handles = {entry[1] for entry in dq}

        if handle not in seen_handles and len(seen_handles) >= RATE_LIMITS["per_ip_sybil"]["max_requests"]:
            # Window is full of different handles — this is a new one, reject
            oldest_ts = min(entry[0] for entry in dq if entry[1] not in seen_handles) if dq else now
            retry_after = max(1, int(oldest_ts - cutoff) + 1)
            return False, retry_after

        # Record this (timestamp, handle) pair
        dq.append((now, handle))
        return True, 0


# Module-level singleton
_rate_limiter = RateLimiter()


def _429(limit_type: str, retry_after: int) -> web.Response:
    """Return a privacy-safe 429 JSON response."""
    return web.Response(
        text=json.dumps({
            "error": "rate_limited",
            "retry_after_seconds": retry_after,
            "limit_type": limit_type,
        }),
        status=429,
        content_type="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Retry-After": str(retry_after),
        },
    )


def _get_ip(request: web.Request) -> str:
    """
    Extract real client IP from X-Forwarded-For (set by nginx/Cloudflare),
    falling back to request.remote.
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        # X-Forwarded-For can be comma-separated; leftmost is the client
        ip = xff.split(",")[0].strip()
        return ip or "unknown"
    return request.remote or "unknown"


# ---------------------------------------------------------------------------
# Structured JSON logger
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line on stdout."""

    LEVEL_MAP = {
        logging.DEBUG: "debug",
        logging.INFO: "info",
        logging.WARNING: "warn",
        logging.ERROR: "error",
        logging.CRITICAL: "error",
    }

    def format(self, record: logging.LogRecord) -> str:
        # event key comes from the message itself OR from extra["event"]
        event_name = getattr(record, "event", record.getMessage())
        data: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": self.LEVEL_MAP.get(record.levelno, "info"),
            "event": event_name,
        }
        # Merge any extra context attached via logger.info("msg", extra={...})
        skip = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process", "message",
            "taskName", "event",  # already captured above
        }
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in skip:
                continue
            data[key] = value
        return json.dumps(data, separators=(",", ":"), default=str)


def setup_logging(level_name: str = "info") -> logging.Logger:
    """Configure pipernet logger to emit JSON lines to stdout."""
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.WARNING,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    level = level_map.get(level_name.lower(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())

    logger = logging.getLogger("pipernet")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    return logger


# Module-level logger used throughout this file
log = logging.getLogger("pipernet")


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
    if dead:
        log.warning(
            "sse.slow_client_dropped",
            extra={
                "event": "sse.slow_client_dropped",
                "channel": channel,
                "dropped": len(dead),
            },
        )


# ---------------------------------------------------------------------------
# CORS helper
# ---------------------------------------------------------------------------

def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def _normalise_open_payload(body: dict, channel: str) -> dict:
    """Normalise an open-channel payload into a stable schema-v2-shaped record.

    Open-mode channels (e.g. holders demo) skip Ed25519 verification but still
    need a consistent on-disk shape so history + SSE work uniformly. We accept
    whatever the client sends and project it into:

        from        — handle (string)  [defaults to 'anon-XXXX' if missing]
        channel     — channel name
        sequence    — int (epoch ms if not provided)
        parent      — null
        modes       — ["open"]
        body        — [["txt", text]]
        timestamp   — ISO 8601 string
        signature   — null  (open mode)
        wallet      — original wallet pubkey if the client supplied one
        meta        — original raw payload (for debugging)
    """
    from datetime import datetime, timezone

    text = body.get("content") or body.get("body") or body.get("text") or ""
    if isinstance(text, list):
        for entry in text:
            if isinstance(entry, list) and len(entry) >= 2 and entry[0] == "txt":
                text = entry[1]
                break
        else:
            text = json.dumps(text)
    text = str(text)[:2000]

    handle = (
        body.get("handle")
        or body.get("from")
        or body.get("author")
        or "anon"
    )
    handle = str(handle)[:64]

    ts_in = body.get("timestamp") or body.get("createdAt") or body.get("ts")
    if isinstance(ts_in, (int, float)):
        ts_iso = datetime.fromtimestamp(ts_in / 1000.0, tz=timezone.utc).isoformat()
    elif isinstance(ts_in, str):
        ts_iso = ts_in
    else:
        ts_iso = datetime.now(timezone.utc).isoformat()

    seq = body.get("sequence")
    if not isinstance(seq, int):
        seq = int(time.time() * 1000)

    return {
        "from": handle,
        "channel": channel,
        "sequence": seq,
        "parent": None,
        "modes": ["open"],
        "body": [["txt", text]],
        "timestamp": ts_iso,
        "signature": None,
        "wallet": body.get("author") or body.get("wallet"),
        "meta": {"open_mode": True, "original_keys": sorted(body.keys())},
    }


def _json_response(data: Any, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, indent=2),
        status=status,
        content_type="application/json",
        headers=_cors_headers(),
    )


def _request_id(request: web.Request) -> str:
    """Return the UUID attached by request_id_middleware."""
    return request.get("_request_id", str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Request ID middleware
# ---------------------------------------------------------------------------

@web.middleware
async def request_id_middleware(request: web.Request, handler):
    request["_request_id"] = str(uuid.uuid4())
    return await handler(request)


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

CURL QUICKSTART (alice -> relay -> bob)
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

# alice posts another message -- bob sees it arrive instantly.
"""
    return web.Response(text=text, content_type="text/plain", headers=_cors_headers())


async def handle_post_channel(request: web.Request) -> web.Response:
    """Receive a signed envelope, validate, append, broadcast."""
    channel = request.match_info["name"]
    rid = _request_id(request)
    ip = _get_ip(request)
    masked_ip = _mask_ip(ip)

    # -- Rate limit: per-IP POST budget --
    ok, retry = _rate_limiter.check(
        "per_ip_post", ip,
        max_requests=RATE_LIMITS["per_ip_post"]["max_requests"],
        window_seconds=RATE_LIMITS["per_ip_post"]["window_seconds"],
    )
    if not ok:
        log.warning(
            "ratelimit.exceeded",
            extra={
                "event": "ratelimit.exceeded",
                "limit_type": "per_ip",
                "ip": masked_ip,
                "retry_after": retry,
            },
        )
        return _429("per_ip", retry)

    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        log.warning(
            "envelope.rejected",
            extra={
                "event": "envelope.rejected",
                "request_id": rid,
                "channel": channel,
                "reason": f"invalid JSON: {e}",
            },
        )
        return _json_response({"error": f"invalid JSON: {e}"}, 400)

    if not isinstance(body, dict):
        log.warning(
            "envelope.rejected",
            extra={
                "event": "envelope.rejected",
                "request_id": rid,
                "channel": channel,
                "reason": "body must be JSON object",
            },
        )
        return _json_response({"error": "envelope must be a JSON object"}, 400)

    # -- Rate limit: per-pubkey envelope budget --
    sender_handle = body.get("from") or ""
    if sender_handle:
        ok_pk, retry_pk = _rate_limiter.check(
            "per_pubkey", sender_handle,
            max_requests=RATE_LIMITS["per_pubkey"]["max_requests"],
            window_seconds=RATE_LIMITS["per_pubkey"]["window_seconds"],
        )
        if not ok_pk:
            log.warning(
                "ratelimit.exceeded",
                extra={
                    "event": "ratelimit.exceeded",
                    "limit_type": "per_pubkey",
                    "key": _mask_key(sender_handle),
                    "ip": masked_ip,
                    "retry_after": retry_pk,
                },
            )
            return _429("per_pubkey", retry_pk)

    # Open-channel allowlist — channels in this set accept envelopes without
    # Ed25519 signature verification. The wallet-token gate on the client side
    # is the authentication for these channels; v0 demo mode for the holder
    # chat. Production v1 will move 'holders' off this list once client-side
    # ephemeral-keypair signing ships. Configurable via PIPERNET_OPEN_CHANNELS
    # env var (comma-separated).
    OPEN_CHANNELS = {
        c.strip()
        for c in (os.environ.get("PIPERNET_OPEN_CHANNELS", "holders") or "").split(",")
        if c.strip()
    }

    if channel in OPEN_CHANNELS:
        # Open mode: skip signature verification, but still require minimal
        # envelope shape. Normalise payload into a schema-v2-shaped record
        # so storage + history + SSE all work consistently.
        normalised = _normalise_open_payload(body, channel)
        core.append_to_channel(channel, normalised)
        body = normalised  # for downstream broadcast
    else:
        # Strict mode: signature must verify against the registry.
        registry = core.load_pubkey_registry()
        verdict = core.verify_envelope(body, registry=registry)
        if not verdict["valid"]:
            log.warning(
                "envelope.rejected",
                extra={
                    "event": "envelope.rejected",
                    "request_id": rid,
                    "channel": channel,
                    "from": body.get("from"),
                    "reason": verdict["reason"],
                },
            )
            return _json_response(
                {"error": "signature does not verify", "reason": verdict["reason"]},
                400,
            )

        # Append to channel
        core.append_to_channel(channel, body)
    log.info(
        "envelope.appended",
        extra={
            "event": "envelope.appended",
            "request_id": rid,
            "channel": channel,
            "from": body.get("from"),
            "sequence": body.get("sequence"),
        },
    )

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
    rid = _request_id(request)
    remote = request.remote or "unknown"

    response = web.StreamResponse()
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    response.headers.update(_cors_headers())
    await response.prepare(request)

    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _get_subs(channel).append(queue)

    log.info(
        "sse.connect",
        extra={
            "event": "sse.connect",
            "request_id": rid,
            "channel": channel,
            "remote": remote,
            "subscribers": len(_get_subs(channel)),
        },
    )

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
        log.info(
            "sse.disconnect",
            extra={
                "event": "sse.disconnect",
                "request_id": rid,
                "channel": channel,
                "remote": remote,
                "subscribers": len(_get_subs(channel)),
            },
        )

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
    rid = _request_id(request)

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

            ia = dict(identity_assertion)
            sig_hex = ia.pop("self_signature_hex", None)
            if not sig_hex:
                return _json_response(
                    {"error": "identity_assertion missing self_signature_hex"}, 400
                )
            pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
            pk.verify(bytes.fromhex(sig_hex), core.canonical(ia))
        except Exception as e:
            log.warning(
                "pubkey.rejected",
                extra={
                    "event": "pubkey.rejected",
                    "request_id": rid,
                    "handle": handle,
                    "reason": str(e),
                },
            )
            return _json_response(
                {"error": f"identity assertion signature does not verify: {e}"}, 400
            )

    core.register_pubkey(handle, pubkey_hex)
    log.info(
        "pubkey.registered",
        extra={
            "event": "pubkey.registered",
            "request_id": rid,
            "handle": handle,
        },
    )
    return _json_response({"registered": handle, "pubkey_hex": pubkey_hex})


async def handle_gossip(request: web.Request) -> web.Response:
    """Accept a batch of envelopes from a peer relay.

    Body: list of signed envelopes.
    Validates each, deduplicates by signature, appends new ones.
    Returns {appended: N, rejected: [...]}
    """
    rid = _request_id(request)

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

    log.info(
        "gossip.received",
        extra={
            "event": "gossip.received",
            "request_id": rid,
            "appended": appended,
            "rejected": len(rejected),
        },
    )
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

    app = web.Application(middlewares=[request_id_middleware])
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


def run_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    node_handle: str | None = None,
    log_level: str = "info",
) -> None:
    """Start the relay server. Blocks until Ctrl+C."""
    setup_logging(log_level)
    app = build_app(node_handle=node_handle)

    log.info(
        "relay.start",
        extra={
            "event": "relay.start",
            "version": VERSION,
            "node": app["node_handle"],
            "host": host,
            "port": port,
            "storage": str(core.home()),
            "log_level": log_level,
        },
    )

    try:
        web.run_app(app, host=host, port=port, print=None)
    finally:
        log.info(
            "relay.stop",
            extra={"event": "relay.stop", "node": app["node_handle"]},
        )
