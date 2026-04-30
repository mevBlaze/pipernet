# Roadmap

Each phase has a concrete success criterion, prior art to read first, and an honest difficulty rating.

---

## Phase 0 — Arithmetic Coding Baseline (COMPLETE)

**Status:** Done.  
**Goal:** A correct, runnable arithmetic coder with an order-3 Markov model.  
**Success criterion:** `decode(encode(x)) == x` for arbitrary byte strings.  
**enwik8 target:** No target — establishing the floor.  
**Implementation:** `src/baseline.py` (~280 lines, pure stdlib + numpy)

**What was learned:**
- Standard 64-bit integer arithmetic coding is straightforward and reusable across all future phases.
- Order-3 Markov is a weak model: it captures local patterns but misses long-range dependencies in natural language.
- The bottleneck is the model, not the coder. All future work lives in the model.

**Prior art read:**
- Witten, Neal, Cleary (1987) — "Arithmetic Coding for Data Compression", CACM
- [Arithmetic coding explained simply](https://marknelson.us/posts/2014/10/19/data-compression-with-arithmetic-coding.html) — Mark Nelson

---

## Phase 1 — Order-N Markov (Higher-Order Context)

**Status:** Planned.  
**Goal:** Increase context length to N=6..8, add order-blending (escape mechanism for unseen contexts).  
**Success criterion:** enwik8 compressed to ≤ 35 MB.  
**Expected difficulty:** Low–Medium. Well-understood theory; main challenge is memory efficiency for large N.

**Key ideas:**
- PPM (Prediction by Partial Match): fall back to shorter contexts when the full context is unseen.
- Exclusion principle: counts seen in a longer context exclude symbols from shorter-context predictions.
- Memory: at order-8, context table can grow to hundreds of MB for enwik8. Use a hash map with collision policy.

**Prior art to read:**
- Cleary & Witten (1984) — "Data Compression Using Adaptive Coding and Partial String Matching"
- Moffat (1990) — "Implementing the PPM Data Compression Scheme"
- [PPM overview](https://en.wikipedia.org/wiki/Prediction_by_partial_matching)
- [PPMD implementation reference](https://www.compression.ru/ds/)

**Success gate before moving to Phase 2:** Reproduce gzip-level compression (< 36 MB on enwik8) via a pure Markov approach.

---

## Phase 2 — Context Mixing (PAQ-style)

**Status:** Planned.  
**Goal:** Blend multiple models' predictions using a logistic regression mixer. Each model votes; the mixer learns weights online.  
**Success criterion:** enwik8 compressed to ≤ 22 MB.  
**Expected difficulty:** Medium–High. Context mixing is theoretically clean but requires careful engineering to avoid numerical instability.

**Key ideas:**
- Multiple independent predictors (order-1, order-4, order-8, word-level unigram, etc.) each output a probability.
- A logistic mixer combines predictions: `P = σ(Σ wᵢ · logit(pᵢ))`. Weights updated by gradient descent on each symbol.
- Per-context mixing: the mixer uses the current context as a key, so the blend weights adapt to local statistics.
- PAQ's "secondary symbol estimation" (SSE) step: a lookup table that corrects systematic biases.

**Prior art to read:**
- Mahoney (2005) — "Adaptive Weighing of Context Models for Lossless Data Compression"
- PAQ8 source code (public domain) — [mattmahoney.net/dc/paq8hp12.zip](https://mattmahoney.net/dc/paq8hp12.zip)
- [The PAQ data compression programs](https://mattmahoney.net/dc/paqsouce.html) — Matt Mahoney

**Warning:** PAQ source code is dense. Read the paper first; then read the code as a reference, not as a tutorial.

---

## Phase 3 — Tiny Transformer + Arithmetic Coding (NNCP-style)

**Status:** Planned.  
**Goal:** Replace the Markov model with a small autoregressive transformer that outputs byte probabilities. Compress with arithmetic coding.  
**Success criterion:** enwik8 compressed to ≤ 18 MB.  
**Expected difficulty:** High. Requires training a model, managing inference cost, and fitting the model binary into the Hutter Prize total-size budget.

**Key ideas:**
- NNCP approach: train a character-level language model offline on enwik8 itself (or a representative corpus), then use it as the predictor during compression.
- The Hutter Prize counts the model weights as part of the total compressed size. A 10 MB model + 8 MB compressed data = 18 MB total — competitive.
- Architecture target: 1–5M parameter transformer. Small enough to fit in the budget; large enough to predict well.
- Quantization: 8-bit or 4-bit weights to reduce model size.

**Prior art to read:**
- Bellard (2021) — "NNCP: Lossless Data Compression with Transformer" ([arxiv 2106.06438](https://arxiv.org/abs/2106.06438))
- Wu et al. (2022) — "Transformers are Sample-Efficient World Models"
- [NNCP source](https://bellard.org/nncp/) — Fabrice Bellard

**Constraint:** Hutter Prize CPU time limit is currently 100× real time (i.e., if compression takes 1 hour of wall time, it needs to run in ≤ 100 hours of CPU time). Large transformer inference is slow — profile carefully.

---

## Phase 4 — Post-Training Specialization

**Status:** Research only (no implementation planned yet).  
**Goal:** Fine-tune or compress the model specifically for enwik8-style text to reduce model weight overhead.  
**Success criterion:** Get within 5% of cmix on enwik8 total size.  
**Expected difficulty:** Very High. This is the frontier.

**Key ideas:**
- Distillation: train a small model to mimic a large model's predictions specifically on enwik8.
- Overfitting as a feature: the model is allowed to memorize enwik8 statistics because it will only be used for that file. Prize rules allow this.
- Arithmetic coding with adaptive priors: start with the trained model, update weights during compression (online learning).

**Prior art to read:**
- Mandt et al. (2022) — "Neural Data Compression" survey
- cmix source code — [github.com/byronknoll/cmix](https://github.com/byronknoll/cmix)

---

## What We Are Not Doing (and Why)

- **Lossy compression:** The prize requires lossless. We stay lossless.
- **Domain-specific tricks (HTML stripping, XML preprocessing):** enwik9 is XML-formatted Wikipedia. Pre/post-processing is allowed by prize rules but we defer it to avoid premature optimization.
- **GPU inference during decode:** Prize rules require the decompressor to run on a single CPU core within time limits.

---

*Each phase is gated on the previous: don't start Phase 2 until Phase 1 hits its enwik8 target.*
