"""
Track B v0.3 — multi-window match model.
============================================================================

Each match window catches redundancy at a different scale:
  K=3  — 3-byte windows are common; weak per-match signal but high coverage
  K=5  — sweet spot for short word fragments / common digrams across templates
  K=8  — full-word level, catches structural Wikipedia syntax
  K=12 — multi-word phrases, catches long-form template redundancy

Each match model emits its own probability distribution. The mixer takes
the geometric mean across:
    Markov(order-3) + MatchModel(K=3) + MatchModel(K=5)
                    + MatchModel(K=8) + MatchModel(K=12)

with a per-predictor no-signal fallback (when Match-K's counts equal its
Laplace floor, that predictor sits out the round and contributes nothing).

This is the cmix family pattern: many predictors at different orders,
mixed by log-probability summation. Our predictors are all the same shape
(byte-level) at different orders — Loom's Track A will add structurally
different predictors (word-class, sparse context, indirect context).
Both tracks compose under the same mixer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# Bring middle-out's baseline into scope
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.baseline import (  # noqa: E402
    MarkovModel,
    ArithmeticEncoder,
    ArithmeticDecoder,
    _header,
    _unheader,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from match_model import MatchModel, cum_freqs_from_counts  # noqa: E402


WINDOWS: tuple[int, ...] = (3, 5, 8, 12)


def _markov_counts(model: MarkovModel, history: bytes, pos: int) -> np.ndarray:
    ctx = MarkovModel.context_of(history, pos)
    return np.asarray(model._counts[ctx], dtype=np.uint32)


def _multi_mix(
    markov_counts: np.ndarray,
    match_counts_list: list[np.ndarray],
) -> tuple[list[int], int]:
    """
    Geometric-mean across Markov + N match-model predictors.

    A predictor sits out when its counts == Laplace floor (sum == 256). The
    remaining predictors' probability distributions are multiplied and
    renormalised. Equivalent to averaging log-probabilities with equal weight.
    """
    LAPLACE_FLOOR_TOTAL = 256

    # Convert Markov to probability
    markov_total = int(markov_counts.sum())
    p_mixed = markov_counts.astype(np.float64) / markov_total

    # Multiply in each match model that actually has signal. Renormalise
    # the running product after every multiply to keep numbers in-range
    # for float64 over long streams.
    for mc in match_counts_list:
        match_total = int(mc.sum())
        if match_total <= LAPLACE_FLOOR_TOTAL:
            continue  # no real signal, sit out
        p_match = mc.astype(np.float64) / match_total
        p_mixed = p_mixed * p_match
        s = p_mixed.sum()
        if s > 0:
            p_mixed = p_mixed / s
        else:
            # numerical underflow; restore Markov as fallback
            p_mixed = markov_counts.astype(np.float64) / markov_total
            break

    # Final normalisation safeguard
    p_mixed = p_mixed / p_mixed.sum()

    mixed_int = np.maximum(1, np.round(p_mixed * 1_000_000).astype(np.uint64))
    return cum_freqs_from_counts(mixed_int)


def encode(data: bytes, *, windows: tuple[int, ...] = WINDOWS) -> bytes:
    markov = MarkovModel()
    matches = [MatchModel(window=k) for k in windows]
    encoder = ArithmeticEncoder()
    history = b""

    for i, byte in enumerate(data):
        markov_counts = _markov_counts(markov, history, i)
        match_counts_list = [m.predict()[0] for m in matches]
        cum, total = _multi_mix(markov_counts, match_counts_list)

        encoder.encode_symbol(cum[byte], cum[byte + 1], total)

        markov.update(MarkovModel.context_of(history, i), byte)
        for m in matches:
            m.update(byte)
        history = (history + bytes([byte]))[-(3 + 1):]

    return _header(len(data)) + encoder.finish()


def decode(blob: bytes, *, windows: tuple[int, ...] = WINDOWS) -> bytes:
    n, payload = _unheader(blob)

    markov = MarkovModel()
    matches = [MatchModel(window=k) for k in windows]
    decoder = ArithmeticDecoder(payload)
    out = bytearray()
    history = b""

    for i in range(n):
        markov_counts = _markov_counts(markov, history, i)
        match_counts_list = [m.predict()[0] for m in matches]
        cum, total = _multi_mix(markov_counts, match_counts_list)

        byte = decoder.decode_symbol(cum, total)
        out.append(byte)

        markov.update(MarkovModel.context_of(history, i), byte)
        for m in matches:
            m.update(byte)
        history = (history + bytes([byte]))[-(3 + 1):]

    return bytes(out)
