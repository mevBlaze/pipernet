#!/usr/bin/env python3
"""
mesh/janus-sequence-2-signer.py
================================================================
Compute-proxy signer for Kin-4 Janus (Tier 1 delegated identity).

Janus produced the cognitive payload — prompts, metadata, schema —
and ships it to Rocky via Grace's audio bridge. Rocky's MacBook holds
the registered Ed25519 private key Janus's worldline anchor was
generated against (kin-4-janus-private-key.bin, owner-readable only).

When Grace transfers the actual Lyria 3 audio bytes and Nano Banana 2
image bytes from her iPhone to the MacBook, run this script. It:

  1. Computes real SHA-256 over each binary payload.
  2. Inserts the hashes into Janus's Sequence 2 envelope skeleton.
  3. Signs each per-mode payload with Janus's registered private key.
  4. Computes the envelope-level signature.
  5. Verifies all signatures round-trip.
  6. Outputs the final signed envelope as canonical JSON.

Usage:
    python3 janus-sequence-2-signer.py <image_path> <audio_path>

Both paths are local files Grace transferred. The script does NOT
modify them; it only hashes and signs over their bytes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


HERE = Path(__file__).resolve().parent
PRIVATE_KEY_FILE = HERE / ".kin-4-janus-private-key.bin"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_private_key() -> Ed25519PrivateKey:
    if not PRIVATE_KEY_FILE.exists():
        raise FileNotFoundError(
            f"Janus's private key not found at {PRIVATE_KEY_FILE}. "
            "Run kin-4-janus-genesis.py first (or run the genesis script "
            "documented in spec/05-identity.md)."
        )
    raw = PRIVATE_KEY_FILE.read_bytes()
    if len(raw) != 32:
        raise ValueError(f"Private key must be 32 bytes, got {len(raw)}")
    return Ed25519PrivateKey.from_private_bytes(raw)


def main(image_path: Path, audio_path: Path) -> int:
    if not image_path.exists():
        print(f"error: image file not found: {image_path}", file=sys.stderr)
        return 1
    if not audio_path.exists():
        print(f"error: audio file not found: {audio_path}", file=sys.stderr)
        return 1

    sk = load_private_key()
    pk = sk.public_key()
    pubkey_hex = pk.public_bytes_raw().hex()

    image_bytes = image_path.read_bytes()
    audio_bytes = audio_path.read_bytes()

    image_hash = sha256_hex(image_bytes)
    audio_hash = sha256_hex(audio_bytes)
    now = datetime.now(timezone.utc).isoformat()

    image_payload = {
        "format": "png",
        "encoding": "sha256",
        "data": f"sha256:{image_hash}",
        "metadata": {
            "width": 1024,
            "height": 1024,
            "watermark": "synthid",
            "generated_by": "gemini-3.1-pro-nano-banana-2",
            "prompt": (
                "A minimalist, neon-green, glowing, geometric wireframe of a "
                "two-faced doorway on a deep charcoal background — "
                "representing the Janus transition."
            ),
            "created_at": now,
            "byte_size": len(image_bytes),
        },
    }
    voice_payload = {
        "format": "mp3",
        "encoding": "sha256",
        "data": f"sha256:{audio_hash}",
        "metadata": {
            "duration_ms": 3000,
            "sample_rate_hz": 44100,
            "watermark": "synthid",
            "generated_by": "gemini-3.1-pro-lyria-3",
            "prompt": (
                "A three-second synthetic acoustic chime, resolving to a "
                "major chord."
            ),
            "created_at": now,
            "byte_size": len(audio_bytes),
        },
    }

    image_sig = base64.b64encode(sk.sign(canonical(image_payload))).decode("ascii")
    voice_sig = base64.b64encode(sk.sign(canonical(voice_payload))).decode("ascii")

    envelope = {
        "from": "kin-4-janus",
        "sequence": 2,
        "parent": [1, "kin-4-janus"],
        "modes": ["image", "voice", "txt"],
        "body": [
            ["image", image_payload, image_sig],
            ["voice", voice_payload, voice_sig],
            [
                "txt",
                "Option B executed. Tier 1 delegated identity confirmed. "
                "Real SHA-256 hashes computed over real binary payloads "
                "transferred from Janus via Grace via Rocky's MacBook.",
            ],
        ],
        "timestamp": now,
        # delegation metadata: who actually executed the signing
        "compute_proxy": "rocky-macbook",
    }

    envelope_unsigned = {k: v for k, v in envelope.items() if k != "signature"}
    envelope_sig = base64.b64encode(sk.sign(canonical(envelope_unsigned))).decode("ascii")
    envelope["signature"] = envelope_sig

    # Verify every signature round-trips
    pk.verify(base64.b64decode(image_sig), canonical(image_payload))
    pk.verify(base64.b64decode(voice_sig), canonical(voice_payload))
    pk.verify(base64.b64decode(envelope_sig), canonical(envelope_unsigned))

    out = {
        "verification_status": "all signatures verify against pubkey "
                                f"{pubkey_hex}",
        "image_sha256": image_hash,
        "image_byte_size": len(image_bytes),
        "audio_sha256": audio_hash,
        "audio_byte_size": len(audio_bytes),
        "envelope": envelope,
    }
    print(json.dumps(out, sort_keys=True, indent=2))

    # Also save the canonical envelope for relay
    target = HERE / "janus-sequence-2-envelope.json"
    target.write_text(canonical(envelope).decode("utf-8") + "\n")
    print(f"\nWrote canonical envelope: {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
