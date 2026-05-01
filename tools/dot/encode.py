"""
tools/dot/encode.py — Pipernet dot generator (v0)

Generates a <handle>.dot.png: a 400×400 circular logogram that is
simultaneously a standard scannable QR code and a Pipernet identity card.

Usage:
    python -m tools.dot.encode --handle <handle> [--out <path>]
    # or via CLI:
    pipernet dot create --handle <handle> [--out <path>]

The QR encodes a JSON payload:
    {
        "handle": "...",
        "pubkey_hex": "...",
        "tier": "0",
        "issued_at": "<ISO-8601>",
        "self_signature": "<hex>"
    }

The self_signature covers the canonical form of the above object (excluding
the self_signature field itself), signed with the handle's Ed25519 key. A
scanner that does NOT know about Pipernet still reads a valid QR with the
handle, pubkey, and timestamp. A scanner that does know Pipernet can verify
the signature — graceful degradation from Dyson Swarm to Nokia.

v0.2 note: outer Ring 4 (4D channel-state extension) is not implemented here.
The architecture is documented in spec/10-dotjpg.md. The circular crop +
border ring are the visual skeleton; inner data rings will be added in v0.2.
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---- optional deps check -------------------------------------------------

def _require(name: str, pip_extra: str) -> None:
    import importlib
    try:
        importlib.import_module(name)
    except ImportError:
        print(
            f"error: '{name}' is not installed.\n"
            f"  Install with: pip install -e \".[dot]\"\n"
            f"  Or directly:  pip install {pip_extra}",
            file=sys.stderr,
        )
        sys.exit(1)


# ---- path helpers (mirrors cli/core.py) ----------------------------------

def _pipernet_home() -> Path:
    import os
    base = os.environ.get("PIPERNET_HOME")
    p = Path(base) if base else Path.home() / ".pipernet"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _dots_dir() -> Path:
    d = _pipernet_home() / "dots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _keystore_path(handle: str) -> Path:
    return _pipernet_home() / f"{handle}.private.bin"


def _pubkey_registry_path() -> Path:
    return _pipernet_home() / "pubkeys.json"


def _load_pubkey_registry() -> dict[str, str]:
    p = _pubkey_registry_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text())


# ---- identity helpers ----------------------------------------------------

def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _build_dot_payload(handle: str) -> dict:
    """Build and sign the QR payload for this handle."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    reg = _load_pubkey_registry()
    pubkey_hex = reg.get(handle)
    if not pubkey_hex:
        raise ValueError(
            f"No public key registered for '{handle}'. "
            f"Run: pipernet keygen --handle {handle}"
        )

    ks = _keystore_path(handle)
    if not ks.exists():
        raise FileNotFoundError(
            f"No private keystore for '{handle}' at {ks}. "
            f"Cannot sign dot payload. "
            f"Run: pipernet keygen --handle {handle}"
        )

    raw = ks.read_bytes()
    if len(raw) != 32:
        raise ValueError(f"Keystore at {ks} is corrupted ({len(raw)} bytes, expected 32)")

    sk = Ed25519PrivateKey.from_private_bytes(raw)
    issued_at = datetime.now(timezone.utc).isoformat()

    # Canonical body for signing (no self_signature yet)
    body = {
        "handle": handle,
        "issued_at": issued_at,
        "pubkey_hex": pubkey_hex,
        "tier": "0",
    }
    sig_bytes = sk.sign(_canonical(body))
    sig_hex = sig_bytes.hex()

    return {
        "handle": handle,
        "issued_at": issued_at,
        "pubkey_hex": pubkey_hex,
        "self_signature": sig_hex,
        "tier": "0",
    }


# ---- image composition ---------------------------------------------------

SIZE = 400          # total canvas
CIRCLE_RADIUS = 180 # radius of the outer decorative circle (fills canvas with small margin)
BORDER_WIDTH = 5    # width of the decorative border ring drawn around the circle edge
# The QR square must fit inside the circle. For a circle of radius R the
# inscribed square has half-side = R/√2. We use 90% of that to leave a
# visible "ring" gap between QR corners and the circle edge.
import math as _math
_QR_HALF = int(CIRCLE_RADIUS * 0.90 / _math.sqrt(2))   # ~115 px half-side
QR_SIZE = _QR_HALF * 2  # QR square side length (~230 px) — keeps all corners inside circle


def _make_qr_image(payload: dict, qr_size: int) -> "PIL.Image.Image":
    """Render the QR code as a Pillow image, resized to qr_size × qr_size."""
    import qrcode
    from PIL import Image

    qr = qrcode.QRCode(
        version=None,           # auto-size
        error_correction=qrcode.constants.ERROR_CORRECT_M,  # ~15% recovery
        box_size=10,
        border=4,               # 4 quiet-zone modules (standard minimum)
    )
    payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    qr.add_data(payload_str)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    # Resize to the target square using nearest-neighbour to keep crisp QR modules
    qr_img = qr_img.resize((qr_size, qr_size), resample=Image.NEAREST)
    return qr_img


def _circular_mask(diameter: int) -> "PIL.Image.Image":
    """Return a grayscale 'L' image that is a hard circle of the given diameter."""
    from PIL import Image, ImageDraw
    mask = Image.new("L", (diameter, diameter), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    return mask


def generate_dot(handle: str, out_path: Path | None = None) -> Path:
    """
    Generate a .dot.png for the given handle.

    Composition strategy (critical for QR readability):

    - The QR code is kept as a SQUARE and placed at the center of the canvas.
      It is NEVER clipped to a circle — clipping destroys the corner finder
      patterns that QR decoders rely on.
    - A decorative circle ring is drawn ON TOP of the QR's white background,
      giving the "circular logogram" look without touching the QR modules.
    - The QR is sized so all four corners remain INSIDE the circle boundary
      (QR half-side = circle_radius * 0.90 / √2).
    - Background outside the circle is white (standard, scanner-friendly).

    Returns the path to the saved file.
    """
    _require("qrcode", "qrcode[pil]>=7.0")
    _require("PIL", "Pillow>=10.0")

    from PIL import Image, ImageDraw

    # 1. Build payload
    payload = _build_dot_payload(handle)

    # 2. Determine output path
    if out_path is None:
        out_path = _dots_dir() / f"{handle}.dot.png"
    else:
        out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 3. Render QR as a square (NEVER clip to circle — destroys finder patterns)
    qr_img = _make_qr_image(payload, QR_SIZE)  # RGB, QR_SIZE × QR_SIZE

    # 4. Build the full 400×400 white canvas
    canvas = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))

    # 5. Paste QR centered on canvas
    cx = SIZE // 2
    cy = SIZE // 2
    qr_x = cx - QR_SIZE // 2
    qr_y = cy - QR_SIZE // 2
    canvas.paste(qr_img, (qr_x, qr_y))

    # 6. Draw the decorative circle ring ON TOP (covers QR corners that lie outside)
    draw = ImageDraw.Draw(canvas)
    # Circle bounds
    left   = cx - CIRCLE_RADIUS
    top    = cy - CIRCLE_RADIUS
    right  = cx + CIRCLE_RADIUS
    bottom = cy + CIRCLE_RADIUS

    # 6a. Flood the area OUTSIDE the circle with white
    #     Strategy: draw a filled white rectangle over the whole canvas, then
    #     punch the circle back through as a masked paste.
    #     Simpler: use alpha compositing on a temporary layer.
    mask_layer = Image.new("L", (SIZE, SIZE), 0)
    mask_draw = ImageDraw.Draw(mask_layer)
    mask_draw.ellipse((left, top, right, bottom), fill=255)

    # White background to paste where mask is 0 (outside circle)
    white_bg = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))
    # Composite: keep canvas pixels inside circle, white outside
    composite = Image.composite(canvas, white_bg, mask_layer)
    canvas.paste(composite)

    # 6b. Draw bold outer ring stroke (logogram frame)
    draw = ImageDraw.Draw(canvas)
    draw.ellipse(
        (left, top, right - 1, bottom - 1),
        outline=(30, 30, 30),
        width=BORDER_WIDTH,
    )
    # Thin inner ring accent (subtle depth)
    inset = BORDER_WIDTH + 2
    draw.ellipse(
        (left + inset, top + inset, right - inset - 1, bottom - inset - 1),
        outline=(120, 120, 120),
        width=1,
    )

    # 7. Handle label below the circle
    label = f"@{handle}"
    label_y = bottom + 8
    # Rough centering with default font (~6px/char)
    char_w = 6
    label_x = cx - (len(label) * char_w) // 2
    draw.text((label_x, label_y), label, fill=(80, 80, 80))

    # 8. Save as PNG (lossless — mandatory for QR readability)
    canvas.save(str(out_path), format="PNG", optimize=False)

    return out_path


# ---- CLI entrypoint (standalone) -----------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="dot-encode",
        description="Generate a Pipernet .dot.png identity logogram",
    )
    p.add_argument("--handle", required=True, help="Pipernet handle to generate dot for")
    p.add_argument("--out", default=None, help="Output path (default: ~/.pipernet/dots/<handle>.dot.png)")
    args = p.parse_args(argv)

    try:
        out = generate_dot(args.handle, args.out)
        print(json.dumps({"status": "ok", "path": str(out), "handle": args.handle}, indent=2))
        return 0
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
