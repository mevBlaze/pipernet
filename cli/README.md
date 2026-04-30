# pipernet — reference CLI

A runnable Tier 0 client for the Pipernet protocol. Generates Ed25519
keypairs, signs schema-v2.0 envelopes, verifies signatures, maintains a
local append-only channel log. Pure Python, single dependency
(`cryptography`).

## Install

```bash
git clone https://github.com/mevBlaze/pipernet
cd pipernet
pip install -e .
```

That installs the `pipernet` command. (Or run as a module:
`python3 -m cli ...`.)

## Quick start

Generate your keypair (one-time, per handle):

```bash
pipernet keygen --handle alice
```

Output is your **identity assertion** with public key, self-signature,
and creation timestamp. The private key lands at
`~/.pipernet/alice.private.bin` (mode 0600).

Send a signed message and append to your local channel log:

```bash
pipernet send --handle alice --channel room \
              --body "hello pipernet" \
              --append --verify
```

Read the local channel:

```bash
pipernet inbox --channel room
```

You'll see your own message with a `✓` next to it (signature verified).

Verify an envelope from a file:

```bash
pipernet send --handle alice --channel room --body "test" > /tmp/env.json
pipernet verify /tmp/env.json
```

Or from stdin:

```bash
pipernet send --handle alice --channel room --body "test" | pipernet verify -
```

## Multi-user demo

Generate a second identity and exchange messages:

```bash
# bob's machine
pipernet keygen --handle bob

# bob ships his pubkey to alice (Tier A: copy-paste, file transfer, or DOTpost)
pipernet whoami --handle bob

# alice's machine: register bob's pubkey
pipernet register --handle bob --pubkey <bob_pubkey_hex>

# bob writes a message and sends it as text
pipernet send --handle bob --body "hi alice" > msg.json

# alice receives msg.json (via Tier-A transport: AirDrop, scp, etc.)
pipernet verify msg.json
# {
#   "valid": true,
#   "from": "bob",
#   ...
# }
```

## What's a Pipernet envelope

```json
{
  "from": "alice",
  "sequence": 1,
  "parent": null,
  "modes": ["txt"],
  "body": [["txt", "hello pipernet"]],
  "timestamp": "2026-04-30T22:00:00+00:00",
  "signature": "<base64 ed25519 sig over the canonical JSON of all other fields>"
}
```

Schema details: [`spec/04-channel-room.md`](../spec/04-channel-room.md).

## What this is NOT

- **Not networking.** This client doesn't talk to peers over WebRTC
  or HTTP yet. It signs envelopes locally and reads/writes a local
  channel log. Networking is the next milestone — see `ROADMAP.md`.
- **Not a full Pipernet node.** A node would also relay envelopes
  between peers, maintain a federated channel state, and serve a
  query API. This is the *client* — the part that signs and verifies.
- **Not for production secrets.** Keystore is `0600` permissioned but
  unencrypted at rest. Production should use a hardware HSM, OS
  keyring, or encrypted vault.

## What this is

Honest reference code that maps 1:1 to the spec. ~400 lines total.
Read the source; the spec; reproduce the round-trip. The protocol is
not a black box. The verification stage runs on your machine.
