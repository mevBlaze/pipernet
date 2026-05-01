# Contributing

There are two tracks. Pick one, or both.

**Protocol track** — work on the spec, the CLI, the compression engine,
or the mesh layer. Lives in `spec/`, `cli/`, `compression/`, `mesh/`.

**Coin discussion** — goes elsewhere. See [GitHub Discussions](https://github.com/dot-protocol/pipernet/discussions) for protocol questions and roadmap conversations. Coin-specific chatter (price, charts, memes) belongs on [piedpiper.fun/grove](https://piedpiper.fun/grove) and the project's social channels — *not* in this repo's issue tracker.
This repo is not the right place for token-price opinions or pump.fun
strategy.

---

## Setup

```bash
git clone https://github.com/dot-protocol/pipernet
cd pipernet
python -m venv venv && source venv/bin/activate
pip install -e .
```

Verify the CLI works:

```bash
pipernet keygen --handle test
pipernet send --handle test --channel test --body "hello" --append --verify
pipernet inbox --channel test
```

---

## Running tests

```bash
# Protocol / CLI tests
python -m pytest cli/ -v

# Compression benchmark (downloads enwik8 if not present, ~100 MB)
python3 compression/track-b/bench.py 100000

# Full benchmark suite
make benchmark
```

All tests must pass before opening a PR. Round-trip correctness
(`decode(encode(x)) == x`) is a hard gate — no exceptions.

---

## Filing issues

Use GitHub issues. Label as:
- `bug` — something broke
- `question` — you're not sure how something is supposed to work
- `spec` — a question or gap in the protocol specification
- `enhancement` — a feature that doesn't exist yet

One issue per thing. Reproducible cases preferred.

---

## Proposing protocol changes

Protocol changes go through `spec/` PRs.

The bar is higher than a normal code PR:

1. Read the existing spec documents first. Understand why the current
   design is the way it is before proposing to change it.
2. Open an issue labeled `spec` to discuss the problem before writing
   the change. The worst outcome is a spec PR that solves a real problem
   the wrong way.
3. Protocol PRs need: a problem statement, the proposed change, why the
   current behavior is inadequate, and what breaks if you change it.
4. Reference implementations are welcome alongside spec changes, but the
   spec text is the authoritative change — the code follows.

---

## Commit message conventions

Imperative mood. Short subject line (under 72 characters). That's it.

Good: `add order-6 Markov context to compression baseline`
Good: `fix Ed25519 verify to reject tampered payload field`
Fine: `update ROADMAP phase 1 status`
Skip: `WIP`, `fix stuff`, `changes`

No ticket numbers required. No heavy-handed scope prefixes required.
If the commit is self-explanatory from the subject line, that's enough.

---

## Code style

- Python: `black` for formatting, no other strong opinions.
- JavaScript/TypeScript: `prettier` for formatting, no other strong
  opinions.
- Readability over cleverness — this is protocol-adjacent code, people
  will be reading it to understand the spec.
- Self-tests in new modules. If you add a new model or a new CLI
  command, add something runnable under `if __name__ == "__main__":` or
  a pytest file.

---

## What we're looking for

Good PRs:
- Improve compression ratio with a benchmark before/after
- Implement a spec section that's currently designed but not coded
- Fix a correctness bug with a regression test
- Add a missing spec clarification with a concrete example
- Improve error messages in the CLI

Out of scope:
- Changing the `encode`/`decode` interface without a design issue first
- Swapping backend language without a design discussion
- Anything that requires a centralized server to work
- Token/coin mechanics — that's not in this repo
