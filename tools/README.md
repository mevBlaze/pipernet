# pipernet/tools

Utilities for the Pipernet protocol ecosystem.

---

## council-to-dot.py

Converts a Claude Code session transcript into signed Pipernet DOT envelopes.

### What it does

1. Reads a Claude Code session JSONL (`~/.claude/projects/…/*.jsonl`)
2. Extracts every `user` and `assistant` message
3. Builds a schema v2.0 DOT envelope for each message — signed with Ed25519
4. Writes the envelopes as append-only JSONL to `~/.pipernet/channels/council-<session-id>.jsonl`

The keypair is generated once per session and stored at `~/.pipernet/sessions/<session-id>.bin`. Re-running the script on the same file reuses the same keypair (idempotent handles, consistent pubkeys).

Both `grace` (human) and `rocky` (assistant) messages become envelopes under the same session keypair, with role-suffixed handles: `council-<id>-grace` and `council-<id>-rocky`.

Timestamps are taken from the source transcript, not the current clock — so the chain reflects the real session time.

### How to run

```bash
# From the pipernet repo root:

# Explicit file:
python3 tools/council-to-dot.py ~/.claude/projects/-Users-blaze-Movies-Kin/<session-id>.jsonl

# Auto-select most recent session:
python3 tools/council-to-dot.py
```

### Output

```
Processed  42 messages  →  42 envelopes
Channel    /Users/you/.pipernet/channels/council-fe04b6bc.jsonl
Handle     council-fe04b6bc   pubkey=594a44e4f...
```

### Verifying envelopes

```python
import json, sys
sys.path.insert(0, '/path/to/pipernet')
from cli.core import verify_envelope
from pathlib import Path

channel = Path.home() / '.pipernet/channels/council-fe04b6bc.jsonl'
for line in channel.read_text().splitlines():
    env = json.loads(line)
    result = verify_envelope(env)
    print(result['valid'], env['sequence'], env['from'])
```

### Dependencies

Only what's already in `pyproject.toml`:
- `cryptography>=42.0` (Ed25519 signing)
- Python 3.10+

---

## How this fits the bigger picture

This script is the **seed** of the live council channel that `openpiper-v2` will eventually display.

The pipeline:

```
Claude Code session  →  council-to-dot.py  →  JSONL channel
                                                     ↓
                                           openpiper-v2 viewer
                                           (live channel — coming soon)
```

When the live viewer ships, it will subscribe to the `council` channel over the Pipernet mesh and render envelopes in real time. Every word deliberated during protocol development will be permanently on-chain, cryptographically signed, append-only.

The room is where the protocol is built in public.
Every message is a DOT.

---

## test_council_fixture.jsonl

A minimal 3-message fake session used to smoke-test `council-to-dot.py` without touching real session data. Run:

```bash
python3 tools/council-to-dot.py tools/test_council_fixture.jsonl
```
