# THREATS.md

> What we defend against, what we don't, and why.

The protocol's job is to be honest about its security posture. Sana
called this in R88: *"We'd rather be called naive than caught lying."*

## What Pipernet protects against

| threat | mechanism |
|---|---|
| Message tampering | Ed25519 signature over (sender, sequence, parent, body, timestamp) |
| Message reordering / replay | Lamport sequence per source + Merkle ancestry chain |
| Impersonation in a public channel | Behavioral signature (continuous statistical fingerprint per source) |
| Server reading message contents | E2E encryption at the FNP layer (Tier 1, opt-in) |
| Server selling user data | Architecture cannot retain it — state lives on-device, never on server |
| Spam / sybil flooding | Depth-as-bandwidth: shallow signatures auto-rate-limited; deep signatures unlimited |
| Vendor lock-in | Worldline-based identity (your keys, your address); protocol is open standard |
| Single-operator failure | Federated by design; any peer can run a node; no central server |

## What Pipernet does NOT yet protect against

We say this clearly because hiding gaps is the vector by which protocols
become Hooli over time.

| gap | mitigation in roadmap |
|---|---|
| Compromised endpoint device | TLS-style key rotation; recovery via worldline (Recovery = Identity) |
| Government-level traffic analysis | Onion routing / Tor-style mixnet integration; design pending |
| Quantum-era signature break | Post-quantum signature migration when standards stabilize (NIST PQC) |
| Bot-driven depth simulation | Behavioral fingerprint defended; ongoing arms race expected |
| Human social engineering of Recovery | Multi-party recovery (m-of-n trusted endpoints); design pending |

## Trust model

- **Trust no one's writes.** Every message is signed; signatures are
  verified before display in any client.
- **Trust the math.** Ed25519, SHA-256, well-vetted primitives only.
- **Trust no single operator.** Including ourselves. The protocol must
  outlive the maker.

## Threat-model audit cadence

Per-release. Every PR that touches `spec/`, `compression/`, or
`mesh/` lists which entries in this document it affects. New gaps are
added immediately; closed gaps move from "does NOT yet protect" to
"protects" with a link to the implementing change.

## Bug bounty

When the foundation forms, a public bounty for protocol-level
vulnerabilities. Until then, responsible disclosure to a contact in
the foundation charter (TBD).

## What we are still working on

- Replay protection across mesh boundaries (Lamport ordering is local
  to each source; cross-source ordering is currently best-effort by
  timestamp).
- The 200-byte self-bootstrapping grammar — designed in the founding
  conversations, not yet shipped.
- WebRTC P2P transport — currently relay-mediated.
- SMS gateway proof of concept — Nokia 3315 floor is in the framing,
  not yet implemented.

If you find a gap not listed here, open an issue. **Honesty rule applies
to threat models the same way it applies to benchmarks.**
