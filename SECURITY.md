# Security Policy

## Reporting a vulnerability

**Primary channel — GitHub Security Advisories.**
[github.com/dot-protocol/pipernet/security/advisories/new](https://github.com/dot-protocol/pipernet/security/advisories/new)

This is the recommended path. Reports stay private between you, the
maintainers, and any collaborators we explicitly add. GitHub will assign a
CVE if applicable. We monitor this channel and acknowledge within 72 hours.

**Backup channel — direct email.**
A dedicated `security@piedpiper.fun` mailbox is being set up. Until that
lands, the GitHub Security Advisory flow above is the supported way to
reach us. Do not file public GitHub issues for security findings — give us
a chance to fix before the details are public.

---

## Disclosure window

90 days from the date you first report to us.

We will acknowledge within 72 hours and give you a status update at
30 days. If we need more time, we'll tell you why and what the timeline
is. At 90 days, you're free to disclose regardless of our status.

We will credit you in the fix unless you ask us not to.

---

## Scope

**In scope:**

- `cli/` — the pipernet command-line client (keygen, send, verify,
  inbox, register)
- `spec/` — protocol specification documents; issues where the spec
  describes behavior that is insecure by design
- `compression/` — the compression engine; issues where the codec
  produces incorrect output or is exploitable during decompression
- `mesh/` — the channel room tooling

**Out of scope:**

- The $PIPER memecoin contract on Solana. That is a separate artifact on
  a separate chain. It has not been audited. Buyer beware is the entire
  security model for the token. Do not send us vulnerability reports for
  the pump.fun contract — we cannot patch it, and we are not responsible
  for it.
- pump.fun itself, or any third-party exchange or interface
- Social engineering attacks on contributors
- Theoretical attacks with no working proof of concept

---

## What we care about

The threat model for the protocol centers on:

- **Signature forgery** — an attacker constructing a valid-looking
  envelope that passes `pipernet verify` without the real private key
- **Identity confusion** — registering a handle or pubkey in a way that
  impersonates another participant
- **Append-only violations** — attacks that allow retroactive deletion or
  modification of channel history
- **Key material exposure** — bugs in keygen or key storage that weaken
  the Ed25519 keypair
- **Denial of service against a local node** — crashes or resource
  exhaustion from malformed input

If you found something in one of these categories, we want to hear from
you.
