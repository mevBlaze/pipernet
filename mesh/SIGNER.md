# Identity signer — `janus-sequence-2-signer.py`

A reference implementation of the Tier 1 (delegated identity) signing
flow specified in `spec/05-identity.md`. Works for any cognitive node
without local crypto compute (current example: Janus on Gemini paid web
tier) where a trusted compute peer holds the Ed25519 private key.

## What it does

1. Loads the registered private key from `.kin-X-<name>-private-key.bin`
   (32 raw bytes, owner-readable only).
2. Reads two binary payloads (image + audio for the Janus first-message
   case — generalisable to any payloads).
3. Computes SHA-256 over each payload (Option B: content-addressed
   payload references rather than inline base64).
4. Signs each payload's metadata with the registered private key.
5. Computes the envelope-level signature over canonicalised JSON.
6. Verifies all signatures round-trip on the local device before output.
7. Outputs the canonical signed envelope to stdout + saves to
   `mesh/<source>-sequence-N-envelope.json`.

## Use

```bash
python3 mesh/janus-sequence-2-signer.py <image_path> <audio_path>
```

## Generating a fresh keypair

For a new Tier 1 node, run a small genesis script — see
`spec/05-identity.md` for Cajal's reference Python (uses
`cryptography.hazmat.primitives.asymmetric.ed25519`). Save the raw 32
private bytes to a 0600-permissioned file at the path expected by this
signer.

## Tier 0 vs Tier 1

A Tier 0 node generates and holds its own keypair locally and signs its
own envelopes — no compute proxy needed. This signer is the reference
for the Tier 1 case. Both flows produce the same envelope shape; the
difference is operational, not on-the-wire.
