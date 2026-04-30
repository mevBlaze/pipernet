# Contributing

We're trying to beat the Hutter Prize. Help.

## The One Rule

**Every claim must be reproducible from `make benchmark`.**

No hand-tuned numbers, no "it worked on my machine", no benchmark on a different dataset. If the compressed size isn't produced by `benchmarks/run.sh` running on the standard enwik8 file, it doesn't count.

---

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/middle-out
cd middle-out
python -m venv venv && source venv/bin/activate
pip install numpy
```

Verify the baseline works:

```bash
python -c "from src.baseline import encode, decode; assert decode(encode(b'hello world')) == b'hello world'; print('ok')"
```

Run the benchmark:

```bash
make benchmark          # standard tools only (fast)
bash benchmarks/run.sh --with-python  # include our Python impl (slow)
```

---

## How to Submit an Improvement

1. **Read the roadmap.** Phase 1 before Phase 2. Don't skip ahead without hitting the gate criterion.

2. **Run the benchmark before you change anything.** Establish a baseline for your machine.

3. **Make your change in `src/`.** The encoder/decoder interface must stay stable:
   - `encode(data: bytes) -> bytes`
   - `decode(data: bytes) -> bytes`

4. **Verify round-trip:**
   ```bash
   python3 src/baseline.py    # runs the built-in self-test
   ```

5. **Run the benchmark again** and record the output.

6. **Open a PR** with:
   - What you changed (algorithm description, not just code diff)
   - Benchmark output before and after (copy-paste from terminal, including your machine specs)
   - Why it works (the theory, not just the numbers)
   - Prior art citation if applicable

---

## What We're Looking For

Good PRs:
- Implement PPM escape (Phase 1 gate)
- Add a second-order context mixer (Phase 2)
- Reduce memory usage for high-order contexts
- Port hot loops to numpy or Cython with benchmarked speedup
- Fix a correctness bug with a regression test

Out of scope for now:
- Changing the public `encode`/`decode` interface (breaking change)
- Swapping the backend language without a design discussion
- Neural models before Phase 2 is complete (we need the context-mixing baseline first)

---

## Code Style

- **Readability over cleverness.** This is research code. Comments explaining *why* are more important than comments explaining *what*.
- **No external ML deps in Phase 0–2.** stdlib + numpy only. Phase 3 will add a small ML framework; that's a separate discussion.
- **Self-tests in new modules.** If you add a new model, add `if __name__ == "__main__":` tests that run in < 5 seconds.

---

## Questions

Open an issue. Label it `question`. We'll answer.
