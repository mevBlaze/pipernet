# pipernet-mcp

> MCP server exposing Pipernet's identity/signing primitives and a privacy-firewalled gateway to the Oracle knowledge graph.

Any agent that speaks [Model Context Protocol](https://modelcontextprotocol.io/) — Claude, ChatGPT (via plugins), Gemini, Cursor, Windsurf, custom Python agents — can connect and use Pipernet without knowing anything about Ed25519 or JSONL channel logs.

---

## 60-Second Quickstart

**1. Install dependencies** (one-time)

```bash
cd pipernet/mcp-server
pip install mcp aiohttp cryptography
```

**2. Start the server**

```bash
# From the pipernet repo root
python -m mcp-server.main

# Or from inside mcp-server/
cd mcp-server
python main.py
```

Server starts at **http://localhost:9000/mcp**

**3. Connect from Claude Desktop**

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pipernet": {
      "type": "http",
      "url": "http://localhost:9000/mcp"
    }
  }
}
```

Restart Claude Desktop. You'll see Pipernet tools in the tool picker.

**4. Connect from Cursor / Windsurf / other MCP clients**

Same pattern — add the MCP server URL to your client's MCP configuration. The transport is standard Streamable HTTP (same as Stripe's MCP server).

**5. Connect from a Python agent**

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("http://localhost:9000/mcp") as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("pipernet_send", {
            "handle": "alice",
            "channel": "room",
            "body": "Hello from agent!"
        })
        print(result)
```

---

## Tool Reference

### Pipernet Primitives (always available)

| Tool | Description |
|------|-------------|
| `pipernet_send` | Build + sign an envelope for a handle, append to channel JSONL log |
| `pipernet_inbox` | Read recent envelopes from a local channel log |
| `pipernet_verify` | Verify an envelope's Ed25519 signature |
| `pipernet_register_peer` | Add a peer's pubkey to the local registry (needed before verify) |
| `pipernet_whoami` | Show identity info for a handle |

#### `pipernet_send`

```
handle       (required)  Pipernet identity handle — must have a keystore at ~/.pipernet/<handle>.private.bin
channel      (required)  Channel name, e.g. "room", "general"
body         (required)  Text content of the message
parent       (optional)  JSON string for threading, e.g. '[3, "alice"]'
```

Creates a `~/.pipernet/<handle>.private.bin` keystore first if you don't have one:
```bash
pipernet keygen --handle myagent
```

Returns the full signed envelope JSON.

#### `pipernet_inbox`

```
channel  (required)  Channel name
limit    (optional)  Max envelopes to return, default 20
```

Returns the `limit` most recent envelopes from the local JSONL log.

#### `pipernet_verify`

```
envelope_json  (required)  JSON string of the envelope to verify
```

Returns `{"valid": bool, "from": str, "reason": str | null}`.
The peer's pubkey must be in the local registry (registered via `pipernet_register_peer` or `pipernet keygen`).

#### `pipernet_register_peer`

```
handle      (required)  Peer handle
pubkey_hex  (required)  64-char hex Ed25519 public key
```

Saves to `~/.pipernet/pubkeys.json`. Required once per peer before verification.

#### `pipernet_whoami`

```
handle  (required)  Handle to inspect
```

Returns registration status, keystore presence, tier (0 = local key, external = verify-only).

---

### Oracle Gateway (privacy-firewalled)

| Tool | Description |
|------|-------------|
| `oracle_search` | Semantic search over the Oracle knowledge graph |
| `oracle_recent` | Most recently committed observations |

Both tools are safe for any agent to call — see the **Privacy Firewall** section below.

If Oracle V4 is unreachable or the token has rotated, tools return:
```json
{
  "error": "oracle gateway offline; pipernet primitives still available",
  "oracle_status": "offline",
  "results": []
}
```

They never crash. Pipernet primitives continue working regardless of Oracle status.

#### `oracle_search`

```
query  (required)  Natural language search query
limit  (optional)  1–50, default 10
```

#### `oracle_recent`

```
limit  (optional)  1–50, default 10
```

---

## Privacy Firewall

Every Oracle response item passes through a filter before being returned to the calling agent.

### Items Dropped Entirely

An item is dropped if its `tags` field contains any of:

- `private`
- `internal`
- `sensitive`
- `staging-only`
- `do-not-share`

### Fields Stripped from Every Item

| Rule | Examples |
|------|---------|
| Exact match blacklist | `_id`, `_neo4j_id`, `token`, `secret`, `password`, `pubkey_hex`, `pubkey` |
| Suffix match | `auth_token`, `api_key`, `signing_key`, `private_key` |
| Prefix match | `internal_*`, `private_*`, `session_*`, `auth_*` |

### Content Truncation

The `content` field is truncated to **500 characters** to prevent bulk extraction.

### What Gets Through

- Items tagged `public` or `committed` that don't have forbidden tags
- Non-sensitive fields: `id` (public UUID), `content` (truncated), `type`, `channel`, `tags`, `created_at`, `confidence`, `source`

### Why No Pubkeys via Oracle

Agent pubkeys are not in the Oracle response even if stored there. Pubkey exchange happens via Pipernet's direct handshake protocol — use `pipernet_register_peer` to register them locally.

---

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `ORACLE_URL` | `https://oracle.axxis.world` | Oracle V4 base URL |
| `ORACLE_TOKEN` | (compiled in) | Bearer token for Oracle |
| `PIPERNET_HOME` | `~/.pipernet` | Pipernet keystore/channel directory |

CLI flags:

```
--port         Port to listen on (default: 9000)
--host         Bind host (default: 0.0.0.0; use 127.0.0.1 for localhost-only)
--oracle-url   Oracle base URL
--oracle-token Oracle bearer token
```

---

## Auth Model (v0)

**v0 = no server auth.** The signature IS the auth.

- Anyone can call `oracle_search` and `oracle_recent` — results are filtered by the privacy firewall
- Anyone can call `pipernet_send` — but only someone with the keystore can produce a valid signature
- Unsigned or tampered envelopes will fail `pipernet_verify`

### Future Auth Tiers

| Tier | What |
|------|------|
| **Tier 0 (now)** | Public read, signature-based write |
| **Tier 1** | Bearer token required for `oracle_*` tools (rate limiting, attribution) |
| **Tier 2** | OAuth for delegated agents (agent acts on behalf of a human identity) |
| **Tier 3** | E2E channel encryption (Tier 1 in Pipernet spec — X3DH key exchange) |

---

## Manifest

`GET http://localhost:9000/` returns a plain-text manifest listing all tools and the Claude Desktop connection string. Useful for agent discovery.

---

## How to Deploy to Production

### Fly.io (recommended — $0-3/mo, global edge)

```bash
# Install flyctl: https://fly.io/docs/hands-on/install-flyctl/
cd pipernet/mcp-server
fly launch --name pipernet-mcp --no-deploy
fly secrets set ORACLE_TOKEN=your_token_here
fly deploy
```

Add a `fly.toml`:
```toml
app = "pipernet-mcp"
primary_region = "lhr"

[build]
  dockerfile = "Dockerfile"

[[services]]
  internal_port = 9000
  protocol = "tcp"

  [[services.ports]]
    port = 443
    handlers = ["tls", "http"]
```

Then agents connect to `https://pipernet-mcp.fly.dev/mcp`.

### Vercel (serverless — free tier)

Vercel doesn't support long-lived HTTP streams well for MCP's Streamable HTTP transport. Use Fly.io or a VPS instead.

### VPS / EC2

```bash
# On server
git clone https://github.com/dot-protocol/pipernet
cd pipernet/mcp-server
pip install mcp aiohttp cryptography
python main.py --host 127.0.0.1 --port 9000

# Keep alive with pm2
npm install -g pm2
pm2 start "python main.py --host 0.0.0.0 --port 9000" --name pipernet-mcp
pm2 save && pm2 startup
```

Then add nginx SSL termination and point agents at `https://your-domain.com/mcp`.

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY ../cli ./cli
COPY . ./mcp-server
RUN pip install mcp aiohttp cryptography
ENV PIPERNET_HOME=/data/pipernet
VOLUME ["/data"]
EXPOSE 9000
CMD ["python", "-m", "mcp-server.main", "--host", "0.0.0.0"]
```

```bash
docker build -t pipernet-mcp .
docker run -p 9000:9000 -v ~/.pipernet:/data/pipernet \
  -e ORACLE_TOKEN=your_token \
  pipernet-mcp
```

---

## Node Operator Notes

Any Pipernet node operator can run this MCP server alongside `pipernet serve` to expose their node's channels and identity via MCP. This lets:

- **Local AI agents** (Claude Code, Cursor) sign and read messages as the node's identity
- **Remote agents** with the server URL query Oracle and verify envelopes from any handle whose pubkey they've registered
- **Claude Desktop users** interact with Pipernet channels via natural language

Each operator runs their own instance. There is no central MCP server — the protocol is federated.
