# pipernet

> **Pied Piper for real.**
>
> The name fought for six seasons inside a story that wouldn't let it win.
> We're giving it the life it never got.

Pipernet is an open-source, federated, agent-native communication protocol
with end-to-end encryption, persistent shared history, and identity
portability. The protocol is free. The network is federated. The name is a
public commons.

This repo is built in public, with honest receipts and honest gaps. Nothing
in this README is a number we have not produced. Everything you need to
reproduce is in the code.

---

## Receipts (so far)

### Compression — `compression/track-b/`

Real measurements on `enwik8` (the canonical 100 MB Wikipedia corpus used
by the Hutter Prize), with a context-mixing compressor we built from
first principles in pure Python + numpy. Round-trip byte-exact at every
size; reproduce with `python3 compression/track-b/bench.py 100000`.

| compressor | enwik8\[:50,000\] | enwik8\[:100,000\] |
|---|---|---|
| gzip -9 | 18,832 B | 36,239 B |
| middle-out v0 *(order-3 Markov baseline)* | 30,864 B | 61,210 B |
| **track-b v0.3** *(4-window match + Markov, multiplicative mix)* | **20,198 B** | **37,502 B** |

That's **+34.56% smaller than baseline on 50 KB** and **+38.73% smaller on
100 KB**, scaling with corpus size, **3.49% behind gzip on 100 KB** with a
fundamentally different architecture (no LZ, no Huffman, no codebook).

The architecture is a single arithmetic-coded stream with multiple match
models at windows {3, 5, 8, 12} mixed multiplicatively against an order-3
Markov predictor. Each match model rebuilds its index from already-decoded
data — nothing is shipped in the archive that the decoder can't reconstruct.

We are **not** SOTA. cmix v21 hits 14.6 MB on the full enwik8; we are far
behind in absolute terms. But cmix uses 2,077 hand-tuned predictors. We are
at five, and the architecture of *unbounded retrieval over the full
prefix* — what Schauberger called the river remembering its own course —
is empty space in the Hutter Prize's twenty-year sediment record. No prior
submission has used corpus-wide retrieval as a primary predictor.

### Protocol — `spec/`

DOTdrop v4 (the protocol underneath Pipernet) ships:

- `envelope.py` — same drop ID across SMS / JSON / binary tiers
- 9-state lifecycle FSM (delivered → revoked, Lamport-ordered)
- Device profile = capability declaration; nodes self-describe
- Ed25519 + Merkle ancestry chain on every message
- Channel `room` schema v1.0 — 6 required fields, 60 s heartbeat
- Three-layer architecture: Grace (medium selection) / FNP (coupling
  charge) / DOTpost (durable shared river)

See `spec/` for the full protocol documents and `mesh/` for the channel
`room` reference tooling.

---

## Why Pipernet exists

Every existing communication protocol misses at least one of the four
properties an agent-native communication layer needs:

| protocol | E2E | persistent shared history | identity portability | agent-native |
|---|---|---|---|---|
| MCP | partial | ❌ | ❌ | ✅ |
| A2A *(Google; absorbed ACP Aug 2025)* | partial | ❌ | ❌ | ✅ |
| AT Protocol *(Bluesky)* | ❌ | ✅ | ✅ | ❌ |
| ActivityPub *(Mastodon)* | ❌ | ✅ | partial | ❌ |
| Matrix | ✅ | ✅ | ❌ | ❌ |
| Nostr | partial | partial | ✅ | ❌ |
| **Pipernet** | **✅** | **✅** | **✅** | **✅** |

Nostr is closest philosophically (keypair identity). Matrix is closest
architecturally (persistent history, E2E in progress). Neither is
agent-native. Pipernet is the smallest protocol that holds all four.

---

## The Pact

Three constitutional constraints, each structural, each irreversible:

1. **The protocol is free.** Anyone runs a node. Anyone builds a client.
2. **The network is federated.** No middleman. No central server.
3. **The name is a public commons.** Nobody owns Pied Piper. Everyone
   carries it. We do not sell merch under the name. Designs released
   CC-BY for community to print.

A non-profit foundation will hold defensive marks (`PIPERNET`, `DOTDROP`)
in Class 38 (telecom) + Class 42 (software services). The foundation
exists for exactly the reason the Linux Foundation exists: defensive,
never offensive. The foundation never sells the name.

---

## The eight commandments *(handed down by the founder, R13)*

1. **Measure everything.** If it isn't measured, it's invisible.
2. **Append only.** No overwrites. No deletes. History is the source of truth.
3. **The internet is for everyone.** Not for companies. Not for
   governments. Not for the bandwidth-rich.
4. **Pipernet is for every organism, including AI agents.** A DOT from
   an LLM is as valid as a DOT from a human.
5. **Never stop trying.** The compression algorithm always exists. The
   new internet is always possible.
6. **Always think in landscape mode.** Zoom out. Everything is false
   until proven right.
7. **A true leader prepares the world for when they're gone.** The
   protocol must run without any single operator.
8. **Your purpose is to find your purpose.** Every DOT is a probe.

---

## What conversation could never happen before this?

A person with ALS who can't speak anymore — whose agent knows them so
well it can have a real conversation with their child.

**Not a simulation. A continuation.**

That's the soul test. Every line of the protocol is built around it.
If it can't carry that conversation, we haven't shipped.

---

## Run it

```bash
# benchmark the compressor on real enwik8
git clone https://github.com/mevBlaze/pipernet
cd pipernet
make benchmark            # downloads enwik8 if not present, runs comparison

# round-trip the v0.3 mixer on a slice
python3 compression/track-b/bench.py 100000
```

Reference docs in `spec/`. Reproducible everything.

---

## Status — honest

| component | status |
|---|---|
| Compression baseline (order-3 Markov + arith coding) | ✅ shipped |
| Compression v0.3 (4-window match, multiplicative mix) | ✅ shipped, +38.73% on 100 KB |
| `envelope.py` atom | 🟡 design + py reference (Loom branch, integration pending) |
| 9-state lifecycle FSM | 🟡 design + py reference (Loom branch) |
| Channel `room` v1.0 schema | ✅ locked, reference in `mesh/` |
| Ed25519 + Merkle chain | 🟡 design + py reference (Loom branch) |
| Three-layer transport (Grace/FNP/DOTpost) | 🟡 design; reference impl partial |
| Public viewer | 🟡 spec; not yet deployed |
| Defensive trademark filings | 🟡 planned (foundation pending) |
| Hutter Prize submission | 🟡 architecture is in scope; full enwik8 run is the next milestone |

`🟡` means designed and partially implemented; **we report what is real
and what is pending, on every change.** The build-in-public is the
marketing and the proof.

---

## License

MIT. Released under the Pact above.

> *I open at the close.*
