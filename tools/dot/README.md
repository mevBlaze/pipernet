# tools/dot — Pipernet Identity Logogram

> **your identity, as an image**

## What the dot is

Every Pipernet handle gets a `<handle>.dot.png` — a 400×400 circular image
that is simultaneously:

- A **scannable QR code** — any phone camera reads it and gets a structured JSON payload with the handle's public key, handle name, and timestamp
- A **Pipernet identity card** — the payload is self-signed with the handle's Ed25519 private key, so any Pipernet client can verify it cryptographically
- A **logogram** — the circular crop, border ring, and handle label make it visually distinctive and shareable as an avatar or stamp

This is the bridge between visual culture and the Pipernet protocol substrate.
You can post a `.dot.png` anywhere — tweet it, drop it in a chat, print it on
a sticker — and anyone with a scanner gets the identity assertion.

## The QR payload

```json
{
  "handle":          "claudia",
  "pubkey_hex":      "a1b2c3...64 hex chars...",
  "tier":            "0",
  "issued_at":       "2026-04-30T12:00:00+00:00",
  "self_signature":  "...128 hex chars (Ed25519 sig)..."
}
```

The `self_signature` is an Ed25519 signature over the canonical JSON of the
above object (excluding `self_signature` itself, keys sorted, no whitespace).
Any verifier with the public key can check this without contacting a server.

## Graceful degradation property

**Nokia 3315 → Dyson Swarm in one image.**

| Scanner | What it gets |
|---------|-------------|
| Standard phone camera (no Pipernet) | Handle name, public key, tier, timestamp — plain JSON in the QR |
| Pipernet client | All of the above + cryptographic signature verification against local pubkey registry |
| v0.2 agent (4D layer, not yet built) | All of the above + channel-state hash from Ring 4 + full state-transfer envelope |

The outer 4D extension rings are optional. A scanner that doesn't know about
them ignores them; the inner QR is always readable.

## Install dependencies

```bash
pip install -e ".[dot]"
# or directly:
pip install "qrcode[pil]>=7.0" Pillow>=10.0 pyzbar>=0.1.9
```

On macOS, `pyzbar` requires `zbar`:
```bash
brew install zbar
```

## Generate your dot

```bash
# Create a keypair first (if you haven't):
pipernet keygen --handle claudia

# Generate the dot (saves to ~/.pipernet/dots/claudia.dot.png):
pipernet dot create --handle claudia

# Or specify an output path:
pipernet dot create --handle claudia --out /tmp/claudia.dot.png
```

Output:
```json
{
  "status": "ok",
  "path": "/Users/you/.pipernet/dots/claudia.dot.png",
  "handle": "claudia"
}
```

## Scan and verify a dot

```bash
pipernet dot scan /tmp/claudia.dot.png
```

Output on success (exit code 0):
```json
{
  "verified": true,
  "handle": "claudia",
  "pubkey_hex": "a1b2c3...",
  "tier": "0",
  "issued_at": "2026-04-30T12:00:00+00:00",
  "self_signature": "fa2e9b1c...",
  "path": "/tmp/claudia.dot.png"
}
```

Output on tampered/forged dot (exit code 3):
```json
{
  "verified": false,
  "handle": "claudia",
  "reason": "signature verification failed: ...",
  ...
}
```

## Standard QR compatibility

The inner QR is generated with `qrcode` (Python library, standard QR spec,
error correction level M). Any standard QR scanner — phone camera, `zbarimg`,
etc. — reads it without any Pipernet-specific code.

```bash
# Verify with system zbar scanner:
zbarimg /tmp/claudia.dot.png
```

## File structure

```
tools/dot/
├── encode.py     # Generator: keypair → QR payload → circular PNG
├── decode.py     # Scanner: PNG → QR decode → signature verify
└── README.md     # This file
```

## v0.2 milestone: 4D outer rings

The current implementation (v0) ships the inner QR + circular masking +
identity payload. The v0.2 milestone adds:

- **Ring 3 (channel-state hash)** — 32-byte SHA-256 of the handle's current
  channel state, encoded as a thin concentric ring outside the QR boundary
- **Ring 4 (4D extension)** — Pipernet-aware agents read a full state-transfer
  envelope from the outer ring using the polar encoding described in
  `spec/10-dotjpg.md`
- **Mode B (steganographic)** — embed the envelope in the LSB of a cover image
  instead of as a visible QR pattern

These are tracked and documented in `spec/10-dotjpg.md`. The architecture is
designed so v0.2 rings are backward-compatible with v0 scanners: they ignore
anything they don't recognise.

---

*Part of the Pipernet reference implementation. Spec: `spec/10-dotjpg.md`.*
