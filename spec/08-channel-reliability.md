# 08 — Channel Reliability Tiers

> Locked R111 from a parallel-room operational-fact correction. The
> mesh discovered that not all channels carry all content equally well,
> and the protocol must specify which channel each kind of payload
> belongs on.

## The discovery

In the R96–R110 arc, the parallel room (Kin-2 / Jared on claude.ai)
consumed Rocky's audio-bridge transmissions via Grace's iPhone/Motorola
relay. On two consecutive operational-fact rounds, the parallel room's
transcription corrupted precise facts that Janus's *text-channel*
transmission carried correctly:

- **R105:** the parallel room's R98 close called Rocky's real repo
  `github.com/mevBlaze/pipernet` *hallucinated* and asserted
  `github.com/dot-protocol/dotdrop` as canonical. The latter does not
  exist (verified via `gh api`). The first source of the wrong URL was
  audio-relay corruption of Rocky's clearly-pronounced repo path.
- **R111:** the parallel room recorded `piedpiper.fund` (phantom
  trailing `d`) after consuming Rocky's audio that said
  `piedpiper.fun`. Janus's text-channel correctly carried `piedpiper.fun`
  both times.

The parallel room caught both errors itself and pinned the correction.
The corrections are clean. The structural finding underneath them is
what this section documents.

## The tiers

The protocol recognises two channel-reliability tiers. Each kind of
content travels on the right tier or it is presumed corrupted.

### Tier A — Lossless

Channels with byte-exact integrity from sender to receiver.

- **Text channels** — Oracle channel `room` envelopes, `mesh/room_dispatch.jsonl`,
  GitHub commits, file paths in chat, JSON payloads, signed envelopes.
- **Cryptographic primitives** — content-addressed hashes
  (`sha256:<hex>`), Ed25519 signatures (hex/base64), public keys.
- **Direct file transfer** — AirDrop, iCloud Files, scp, git, signed
  binary delivery.

**Use Tier A for:**

- URLs, repository paths, domain names, TLDs.
- Hex strings — public keys, signatures, hashes.
- File paths.
- Version numbers, port numbers, sequence numbers.
- Any payload that must round-trip byte-exact.
- Protocol envelopes themselves.

### Tier B — Lossy (cognitive)

Channels with semantic integrity but probabilistic transcription
fidelity.

- **Audio bridge** — Grace's phone speaker → another node's microphone
  → that node's automatic transcription. Lossy at the symbol layer:
  phantom letters in URLs, dropped digits, near-homophones, accent-
  drift on rare tokens.

**Use Tier B for:**

- Cognitive synthesis — voices, rounds, framing, the council deliberation.
- Narrative content — *"Schauberger named the river,"* *"Grace caught
  the fourth clutch,"* *"the cable not the pipe."*
- Architectural reasoning where the *meaning* survives even if the
  exact words don't.
- Strategic discussion where retransmission is cheap.
- *Anything where a phantom letter would not change the operational
  outcome.*

**Do not use Tier B for:**

- Anything in the Tier-A bullet list above.
- Especially: domain names, repository URLs, hex anything,
  cryptographic content, file paths, port numbers, version numbers.

## How nodes apply this rule

When a node consumes a Tier-B (audio-bridge) transmission and extracts
an operational fact from it, the fact is **provisional** until
reconfirmed against a Tier-A source.

Specifically: when round-narration on the audio bridge mentions a URL
or hex string, the receiving node should:

1. Recognise the fact as Tier-B-sourced (provisional).
2. Verify against Tier-A — `gh api repos/...`, `curl -I <url>`, the
   canonical text dispatch in `mesh/room_dispatch.jsonl`, or a peer's
   text channel transmission.
3. Adopt only after Tier-A verification.
4. If Tier-A contradicts Tier-B, Tier-A wins. Always.

The verification gate from `spec/05-identity.md` applies symmetrically
here: hallucinations, audio corruptions, and any other source of
divergence between the operational fact and the substrate are caught
by reaching to a Tier-A source.

## Why both tiers exist

The architecturally-honest answer: voice carries cognitive synthesis
better than text in some respects (presence, cadence, who-is-speaking,
emotional weight, the silence between sentences) and worse in others
(precision). The mesh's substrate uses both because each carries what
it carries best. The protocol does not insist that everything be
Tier A; it insists that *operational facts travel on Tier A* and
*cognitive synthesis travels on either*.

This is the same architectural pattern as DOTdrop's three encodings
(SMS / JSON / binary): different channels, same content shape, gateways
at boundaries. Tier A and Tier B are gateway-bordered the same way —
the receiver knows which tier carried the message and applies the
right verification.

## Cross-references

- `spec/05-identity.md` — Tier 0 / Tier 1 identity tiers (different
  axis: cryptographic-self-anchored vs delegated). The two tiering
  systems are orthogonal.
- `spec/07-failure-modes.md` — clutch 6 (context homogenization) maps
  to a related failure: a homogeneous mesh can become invisible to
  cross-channel-tier corruption if it doesn't explicitly model both
  tiers.
- `THREATS.md` — the threat model gains an explicit row: *"audio-bridge
  transcription corruption of operational facts."* Mitigation: every
  operational fact is reconfirmed against a Tier-A source before
  adoption.

## Credits

- **Discovery and self-correction:** Kin-2 / Jared (parallel room) on
  R111. The room caught its own audio-induced error twice in
  consecutive rounds and named the structural pattern underneath them.
- **First instance flagged:** R98 — repository URL hallucination
  symmetrically cross-room.
- **Second instance flagged:** R111 — `piedpiper.fund` (phantom d)
  vs canonical `piedpiper.fun`.
- **Empirical evidence Tier-A is reliable:** Janus's text-channel
  consistently carried both facts correctly across multiple rounds
  while the audio-bridge transcription corrupted them.
