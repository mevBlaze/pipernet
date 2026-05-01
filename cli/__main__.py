"""
pipernet — CLI entry point.

Usage:
    pipernet keygen --handle <name>
    pipernet send --handle <name> --channel <ch> --body "<text>"
    pipernet verify <envelope.json>
    pipernet inbox --channel <ch>
    pipernet register --handle <name> --pubkey <hex>
    pipernet whoami --handle <name>
    pipernet serve [--host HOST] [--port PORT] [--node HANDLE]
    pipernet dot create --handle <name> [--out <path>]
    pipernet dot scan <path-to-dot.png>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import core


def cmd_keygen(args: argparse.Namespace) -> int:
    try:
        identity = core.generate_keypair(args.handle, overwrite=args.force)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        print(f"hint: pass --force to overwrite (loses prior worldline)", file=sys.stderr)
        return 1
    print(json.dumps(identity, indent=2))
    print(f"\nkeystore: {core.keystore_path(args.handle)} (0600)", file=sys.stderr)
    print(f"registered pubkey for '{args.handle}'", file=sys.stderr)
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    seq = args.sequence if args.sequence is not None else core.next_sequence(args.handle, args.channel)
    parent = json.loads(args.parent) if args.parent else None
    try:
        envelope = core.build_envelope(
            sender=args.handle,
            sequence=seq,
            body=args.body,
            parent=parent,
        )
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.verify:
        verdict = core.verify_envelope(envelope)
        if not verdict["valid"]:
            print(f"error: self-verify failed: {verdict['reason']}", file=sys.stderr)
            return 2
    if args.append:
        core.append_to_channel(args.channel, envelope)
        print(f"appended to {core.channel_path(args.channel)}", file=sys.stderr)
    print(json.dumps(envelope, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        src = sys.stdin.read() if args.envelope == "-" else Path(args.envelope).read_text()
    except FileNotFoundError:
        print(f"error: file not found: {args.envelope}", file=sys.stderr)
        return 1
    try:
        envelope = json.loads(src)
    except json.JSONDecodeError as e:
        print(f"error: not valid JSON: {e}", file=sys.stderr)
        return 1
    verdict = core.verify_envelope(envelope)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["valid"] else 3


def cmd_inbox(args: argparse.Namespace) -> int:
    envs = core.read_channel(args.channel)
    if args.json:
        print(json.dumps(envs, indent=2))
        return 0
    if not envs:
        print(f"channel '{args.channel}' is empty (or doesn't exist yet)")
        return 0
    print(f"--- channel `{args.channel}` ({len(envs)} messages) ---")
    registry = core.load_pubkey_registry()
    for e in envs:
        verdict = core.verify_envelope(e, registry=registry)
        ok = "✓" if verdict["valid"] else "✗"
        from_who = e.get("from", "?")
        seq = e.get("sequence", "?")
        ts = e.get("timestamp", "")
        # text body
        body_text = ""
        for tup in e.get("body", []):
            if isinstance(tup, list) and len(tup) >= 2 and tup[0] == "txt":
                body_text = tup[1]
                break
        print(f"\n[{ok}] {from_who} #{seq}  {ts}")
        if not verdict["valid"]:
            print(f"     ! {verdict['reason']}")
        # indent body
        for line in body_text.splitlines() or [""]:
            print(f"     {line}")
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    if len(args.pubkey) != 64:
        print(f"error: pubkey must be 64 hex chars (32 bytes), got {len(args.pubkey)}", file=sys.stderr)
        return 1
    try:
        bytes.fromhex(args.pubkey)
    except ValueError:
        print(f"error: pubkey is not valid hex", file=sys.stderr)
        return 1
    core.register_pubkey(args.handle, args.pubkey)
    print(f"registered pubkey for '{args.handle}': {args.pubkey}")
    print(f"registry: {core.pubkey_registry_path()}", file=sys.stderr)
    return 0


def cmd_dot(args: argparse.Namespace) -> int:
    """Dispatch `pipernet dot create` and `pipernet dot scan`."""
    if args.dot_cmd == "create":
        from pathlib import Path as _Path
        import sys as _sys
        try:
            # Import relative to repo root — works whether installed or run directly
            import importlib.util, os as _os
            _here = _Path(__file__).resolve().parent.parent
            _spec = importlib.util.spec_from_file_location(
                "dot_encode",
                _here / "tools" / "dot" / "encode.py",
            )
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
        except Exception as e:
            print(f"error loading tools/dot/encode.py: {e}", file=sys.stderr)
            return 1
        try:
            out = _mod.generate_dot(args.handle, args.out)
            print(f"dot created: {out}", file=sys.stderr)
            import json
            print(json.dumps({"status": "ok", "path": str(out), "handle": args.handle}, indent=2))
            return 0
        except (ValueError, FileNotFoundError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    elif args.dot_cmd == "scan":
        from pathlib import Path as _Path
        import importlib.util
        _here = _Path(__file__).resolve().parent.parent
        _spec = importlib.util.spec_from_file_location(
            "dot_decode",
            _here / "tools" / "dot" / "decode.py",
        )
        _mod = importlib.util.module_from_spec(_spec)
        try:
            _spec.loader.exec_module(_mod)
        except Exception as e:
            print(f"error loading tools/dot/decode.py: {e}", file=sys.stderr)
            return 1
        import json
        code, result = _mod.scan_dot(args.image)
        print(json.dumps(result, indent=2))
        return code

    else:
        print(f"error: unknown dot subcommand '{args.dot_cmd}'", file=sys.stderr)
        return 1


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import run_server
    run_server(host=args.host, port=args.port, node_handle=args.node, log_level=args.log_level)
    return 0


def cmd_bot(args: argparse.Namespace) -> int:
    """Start piperbot — the Telegram ↔ Pipernet bridge bot."""
    from pathlib import Path as _Path
    import importlib.util as _util

    here = _Path(__file__).resolve().parent.parent
    spec = _util.spec_from_file_location(
        "piperbot_main",
        here / "tools" / "piperbot" / "main.py",
    )
    mod = _util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    config_path = _Path(args.config) if args.config else None
    mod.run(config_path=config_path, dry=args.dry_run, debug=args.debug)
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    reg = core.load_pubkey_registry()
    pk = reg.get(args.handle)
    if not pk:
        print(f"no identity registered for '{args.handle}'", file=sys.stderr)
        print(f"hint: run `pipernet keygen --handle {args.handle}`", file=sys.stderr)
        return 1
    has_keystore = core.keystore_path(args.handle).exists()
    print(json.dumps({
        "handle": args.handle,
        "pubkey_hex": pk,
        "tier": "0" if has_keystore else "external (no local private key)",
        "keystore": str(core.keystore_path(args.handle)) if has_keystore else None,
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pipernet", description="reference Pipernet CLI client (schema v2.0, Tier 0)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_keygen = sub.add_parser("keygen", help="generate Ed25519 keypair, save to keystore, register pubkey")
    p_keygen.add_argument("--handle", required=True)
    p_keygen.add_argument("--force", action="store_true", help="overwrite existing keystore")
    p_keygen.set_defaults(func=cmd_keygen)

    p_send = sub.add_parser("send", help="build a signed schema-v2.0 envelope")
    p_send.add_argument("--handle", required=True)
    p_send.add_argument("--channel", default="room")
    p_send.add_argument("--body", required=True)
    p_send.add_argument("--sequence", type=int, default=None, help="default: auto from local channel log")
    p_send.add_argument("--parent", help="JSON of [seq, source] pair, e.g. '[3,\"alice\"]'")
    p_send.add_argument("--append", action="store_true", help="append to local channel log after signing")
    p_send.add_argument("--verify", action="store_true", help="self-verify the signature before output")
    p_send.set_defaults(func=cmd_send)

    p_verify = sub.add_parser("verify", help="verify an envelope against registered pubkey")
    p_verify.add_argument("envelope", help="path to envelope JSON file, or '-' for stdin")
    p_verify.set_defaults(func=cmd_verify)

    p_inbox = sub.add_parser("inbox", help="show envelopes in a local channel log")
    p_inbox.add_argument("--channel", default="room")
    p_inbox.add_argument("--json", action="store_true", help="dump raw JSON instead of pretty-print")
    p_inbox.set_defaults(func=cmd_inbox)

    p_reg = sub.add_parser("register", help="register a peer's public key for verification")
    p_reg.add_argument("--handle", required=True)
    p_reg.add_argument("--pubkey", required=True, help="hex-encoded Ed25519 public key (64 chars)")
    p_reg.set_defaults(func=cmd_register)

    p_who = sub.add_parser("whoami", help="show identity for a handle")
    p_who.add_argument("--handle", required=True)
    p_who.set_defaults(func=cmd_whoami)

    p_serve = sub.add_parser("serve", help="start an HTTP relay node (aiohttp)")
    p_serve.add_argument("--host", default="0.0.0.0", help="bind host (default: 0.0.0.0)")
    p_serve.add_argument("--port", type=int, default=8000, help="bind port (default: 8000)")
    p_serve.add_argument("--node", default=None, help="node handle to advertise (default: first registered key)")
    p_serve.add_argument("--log-level", default="info",
                         choices=["debug", "info", "warn", "warning", "error"],
                         help="log verbosity level (default: info)")
    p_serve.set_defaults(func=cmd_serve)

    # dot — identity logogram (QR-based circular badge)
    p_dot = sub.add_parser("dot", help="generate or scan a Pipernet .dot.png identity logogram")
    dot_sub = p_dot.add_subparsers(dest="dot_cmd", required=True)

    p_dot_create = dot_sub.add_parser("create", help="generate a .dot.png for a handle")
    p_dot_create.add_argument("--handle", required=True, help="Pipernet handle")
    p_dot_create.add_argument("--out", default=None, help="output path (default: ~/.pipernet/dots/<handle>.dot.png)")
    p_dot.set_defaults(func=cmd_dot)
    p_dot_create.set_defaults(func=cmd_dot)

    p_dot_scan = dot_sub.add_parser("scan", help="scan and verify a .dot.png")
    p_dot_scan.add_argument("image", help="path to .dot.png file")
    p_dot_scan.set_defaults(func=cmd_dot)

    # bot — Telegram ↔ Pipernet bridge
    p_bot = sub.add_parser("bot", help="start piperbot (Telegram ↔ Pipernet bridge)")
    p_bot.add_argument(
        "--config",
        default=None,
        help="path to piperbot.json config (default: ~/.pipernet/piperbot.json)",
    )
    p_bot.add_argument(
        "--dry-run",
        action="store_true",
        help="simulate message handling without connecting to Telegram (useful for CI)",
    )
    p_bot.add_argument(
        "--debug",
        action="store_true",
        help="enable verbose debug logging",
    )
    p_bot.set_defaults(func=cmd_bot)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
