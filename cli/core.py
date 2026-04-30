"""
pipernet.cli.core
============================================================
Reference implementation of the Tier 0 identity flow + schema
v2.0 envelope encoding/decoding/verification, in pure Python.

Spec sources:
  spec/04-channel-room.md  — channel `room` schema v1.0 (text-only)
  spec/05-identity.md      — Tier 0 / Tier 1 identity tiers
  spec/08-channel-reliability.md  — Tier A / Tier B substrate

This module gives a runnable foundation: generate keypair, sign
envelope, verify envelope, append to local channel log, read
local channel log. It does NOT do networking — a Pipernet node
that talks to peers over WebRTC or HTTP is the next milestone.

Channel storage is JSONL: one envelope per line, append-only.
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------

def home() -> Path:
    """Return the pipernet config dir, creating it if needed.

    Default: ~/.pipernet/
    Override with PIPERNET_HOME.
    """
    base = os.environ.get("PIPERNET_HOME")
    p = Path(base) if base else Path.home() / ".pipernet"
    p.mkdir(parents=True, exist_ok=True)
    (p / "channels").mkdir(parents=True, exist_ok=True)
    return p


def keystore_path(handle: str) -> Path:
    return home() / f"{handle}.private.bin"


def pubkey_registry_path() -> Path:
    return home() / "pubkeys.json"


def channel_path(channel: str) -> Path:
    return home() / "channels" / f"{channel}.jsonl"


# ----------------------------------------------------------------------
# Canonicalisation
# ----------------------------------------------------------------------

def canonical(obj: Any) -> bytes:
    """Canonical JSON: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ----------------------------------------------------------------------
# Keypair
# ----------------------------------------------------------------------

def generate_keypair(handle: str, *, overwrite: bool = False) -> dict:
    """Generate an Ed25519 keypair, save private key to keystore, return identity assertion.

    Tier 0 only — node holds its own private key.
    """
    p = keystore_path(handle)
    if p.exists() and not overwrite:
        raise FileExistsError(f"keystore already exists at {p}; use overwrite=True")
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    pk_bytes = pk.public_bytes_raw()
    sk_bytes = sk.private_bytes_raw()

    p.write_bytes(sk_bytes)
    os.chmod(p, 0o600)

    pubkey_hex = pk_bytes.hex()
    identity = {
        "from": handle,
        "pubkey_hex": pubkey_hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tier": "0",
    }
    sig = sk.sign(canonical(identity)).hex()
    identity["self_signature_hex"] = sig

    register_pubkey(handle, pubkey_hex)
    return identity


def load_private_key(handle: str) -> Ed25519PrivateKey:
    p = keystore_path(handle)
    if not p.exists():
        raise FileNotFoundError(
            f"no keystore for handle '{handle}' at {p}. "
            f"Run `pipernet keygen --handle {handle}` first."
        )
    raw = p.read_bytes()
    if len(raw) != 32:
        raise ValueError(f"keystore at {p} is corrupted (got {len(raw)} bytes, expected 32)")
    return Ed25519PrivateKey.from_private_bytes(raw)


def load_pubkey_registry() -> dict[str, str]:
    p = pubkey_registry_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def register_pubkey(handle: str, pubkey_hex: str) -> None:
    """Save a peer's public key. Used both for self (after keygen) and for known peers."""
    reg = load_pubkey_registry()
    reg[handle] = pubkey_hex
    pubkey_registry_path().write_text(json.dumps(reg, sort_keys=True, indent=2))


# ----------------------------------------------------------------------
# Envelope
# ----------------------------------------------------------------------

def build_envelope(
    *,
    sender: str,
    sequence: int,
    body: str,
    parent: list | None = None,
    private_key: Ed25519PrivateKey | None = None,
) -> dict:
    """Build a signed schema-v2.0 envelope (text-only).

    Multimodal modes (image, voice) follow the same shape but require the
    payload-sub-signing flow described in spec/04-channel-room.md.
    """
    if private_key is None:
        private_key = load_private_key(sender)
    timestamp = datetime.now(timezone.utc).isoformat()

    envelope = {
        "from": sender,
        "sequence": sequence,
        "parent": parent,
        "modes": ["txt"],
        "body": [["txt", body]],
        "timestamp": timestamp,
    }
    sig = base64.b64encode(private_key.sign(canonical(envelope))).decode("ascii")
    envelope["signature"] = sig
    return envelope


def sign_envelope(envelope_unsigned: dict, private_key: Ed25519PrivateKey) -> dict:
    """Sign an arbitrary envelope dict (without `signature` field), returning the signed envelope."""
    body = {k: v for k, v in envelope_unsigned.items() if k != "signature"}
    sig = base64.b64encode(private_key.sign(canonical(body))).decode("ascii")
    return {**body, "signature": sig}


def verify_envelope(envelope: dict, *, registry: dict[str, str] | None = None) -> dict:
    """Verify the envelope signature against the registered public key for `from`.

    Returns a verdict dict:
      {"valid": bool, "from": str, "reason": str|None, "details": {...}}
    """
    sender = envelope.get("from")
    if not sender:
        return {"valid": False, "from": None, "reason": "envelope missing 'from' field"}

    if registry is None:
        registry = load_pubkey_registry()
    pubkey_hex = registry.get(sender)
    if not pubkey_hex:
        return {
            "valid": False,
            "from": sender,
            "reason": f"no public key registered for '{sender}' "
                      f"(register with `pipernet register --handle {sender} --pubkey <hex>`)",
        }

    sig_b64 = envelope.get("signature")
    if not sig_b64:
        return {"valid": False, "from": sender, "reason": "envelope missing 'signature'"}

    try:
        pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
        body = {k: v for k, v in envelope.items() if k != "signature"}
        pk.verify(base64.b64decode(sig_b64), canonical(body))
    except Exception as e:
        return {
            "valid": False,
            "from": sender,
            "reason": f"signature verification failed: {type(e).__name__}: {e}",
        }

    return {
        "valid": True,
        "from": sender,
        "reason": None,
        "details": {
            "sequence": envelope.get("sequence"),
            "modes": envelope.get("modes"),
            "timestamp": envelope.get("timestamp"),
            "pubkey_hex": pubkey_hex,
        },
    }


# ----------------------------------------------------------------------
# Channel storage (local)
# ----------------------------------------------------------------------

def append_to_channel(channel: str, envelope: dict) -> None:
    """Append a verified envelope to a local channel JSONL log."""
    p = channel_path(channel)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")


def read_channel(channel: str) -> list[dict]:
    """Read all envelopes from a local channel JSONL log, in append order."""
    p = channel_path(channel)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def next_sequence(handle: str, channel: str) -> int:
    """Compute the next monotonic sequence for `handle` on `channel`."""
    seen = read_channel(channel)
    own = [e.get("sequence", 0) for e in seen if e.get("from") == handle]
    return (max(own) + 1) if own else 1
