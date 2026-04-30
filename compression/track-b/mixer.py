"""
Two-predictor mixer.
============================================================================

Combines order-3 Markov (from middle-out/src/baseline.py) with the match
model (track-b/match_model.py) in a single arithmetic stream.

Mixing strategy v0.1:
  * Linear interpolation in count-space.
  * Each predictor returns counts (Laplace-smoothed). We sum
    weighted counts per byte: mixed[i] = w_match * match_counts[i]
                                       + w_markov * markov_counts[i]
  * Weights are fixed; v0.2 will replace this with logistic mixing on
    bit-level predictions (cmix-style). For v0.1 the mix is dumb-but-correct
    so we can isolate the architectural lift from retrieval alone.

Determinism:
  Both sides rebuild MarkovModel and MatchModel from the same byte
  stream → identical predictions → round-trip.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# Bring middle-out's baseline into scope
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.baseline import MarkovModel, ArithmeticEncoder, ArithmeticDecoder, _header, _unheader  # noqa: E402

from match_model import MatchModel, cum_freqs_from_counts  # noqa: E402


# Mixing weights (v0.2 — adaptive).
# When the match model has zero real matches, its counts == Laplace floor (=1
# everywhere). Linear mixing dilutes the Markov prediction in that case,
# which made v0.1 *worse* than baseline. v0.2 weights each predictor by how
# much real signal it carries beyond its Laplace floor.

# Cap on the match model's weight so the Markov prior isn't overwhelmed
# even when the match model has many strong matches. Tuned by experiment.
W_MATCH_CAP = 0.75


def _markov_counts(model: MarkovModel, history: bytes, pos: int) -> np.ndarray:
    """Return raw count array (256,) for the order-3 Markov context at pos."""
    ctx = MarkovModel.context_of(history, pos)
    counts_dict = model._counts[ctx]
    return np.asarray(counts_dict, dtype=np.uint32)


def encode(data: bytes, *, match_window: int = 6) -> bytes:
    markov = MarkovModel()
    match = MatchModel(window=match_window)
    encoder = ArithmeticEncoder()
    history = b""

    for i, byte in enumerate(data):
        markov_counts = _markov_counts(markov, history, i)
        match_counts, _ = match.predict()
        cum, total = _adaptive_mix(markov_counts, match_counts)

        encoder.encode_symbol(cum[byte], cum[byte + 1], total)

        markov.update(MarkovModel.context_of(history, i), byte)
        match.update(byte)
        history = (history + bytes([byte]))[-(3 + 1):]

    return _header(len(data)) + encoder.finish()


def _adaptive_mix(markov_counts: np.ndarray, match_counts: np.ndarray) -> tuple[list[int], int]:
    """
    Geometric-mean (logistic-style) mix WITH a no-signal fallback.

    When the match model has no real matches, its counts equal the Laplace
    floor (1 each, total=256). Geometric-mean mixing with that uniform
    distribution dampens Markov's prediction toward uniform, regressing
    compression on positions where the match model has nothing to say.

    Fix: when match has no signal beyond its Laplace floor, fall through to
    pure Markov. Otherwise, take the geometric mean of the two probability
    distributions — this is the cmix/PAQ-family mixer (logistic mixing with
    equal weight on log-probabilities).
    """
    LAPLACE_FLOOR_TOTAL = 256  # 1 per byte * 256 bytes
    match_total = int(match_counts.sum())

    # No-signal fallback: pure Markov on positions the match model can't help
    if match_total <= LAPLACE_FLOOR_TOTAL:
        return cum_freqs_from_counts(markov_counts)

    markov_total = int(markov_counts.sum())
    p_markov = markov_counts.astype(np.float64) / markov_total
    p_match = match_counts.astype(np.float64) / match_total

    # sqrt(p_a * p_b) ∝ exp(0.5 (log p_a + log p_b))
    p_mixed = np.sqrt(p_markov * p_match)
    p_mixed = p_mixed / p_mixed.sum()

    mixed_int = np.maximum(1, np.round(p_mixed * 1_000_000).astype(np.uint64))
    return cum_freqs_from_counts(mixed_int)


def decode(blob: bytes, *, match_window: int = 6) -> bytes:
    n, payload = _unheader(blob)

    markov = MarkovModel()
    match = MatchModel(window=match_window)
    decoder = ArithmeticDecoder(payload)
    out = bytearray()
    history = b""

    for i in range(n):
        markov_counts = _markov_counts(markov, history, i)
        match_counts, _ = match.predict()
        cum, total = _adaptive_mix(markov_counts, match_counts)

        byte = decoder.decode_symbol(cum, total)
        out.append(byte)

        markov.update(MarkovModel.context_of(history, i), byte)
        match.update(byte)
        history = (history + bytes([byte]))[-(3 + 1):]

    return bytes(out)
