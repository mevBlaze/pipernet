"""
Retrieval / match model for Track B.
============================================================================

Idea:
  At every byte position p, we look at the last K bytes of context. We hash
  that K-byte window. If we have seen the SAME K bytes before, we look at
  what byte immediately followed each prior occurrence. Those bytes form a
  predictive distribution for what comes next.

  This is structurally identical to the match model in cmix/PAQ — but the
  index is *unbounded* (the entire decoded prefix), not a sliding window.
  That's the architectural difference: our predictor sees template-level
  redundancy across the whole corpus (Wikipedia infoboxes that recur every
  few hundred KB), which a 64KB local window architecturally cannot reach.

Determinism:
  The encoder and decoder both rebuild the index from already-decoded data.
  No part of the index is shipped in the archive; the same bytes flow
  through identical code paths on both sides → identical predictions →
  round-trip.

This is *one* predictor. The mixer combines this with order-3 Markov.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterator
import numpy as np


class MatchModel:
    """
    Hash-based byte predictor.

    Tracks every K-byte context that has appeared in the prefix; for each
    such context, accumulates a Laplace-smoothed count over the byte that
    immediately followed.
    """

    LAPLACE_K = 1  # never zero probability

    def __init__(self, window: int = 8, alphabet: int = 256, max_matches: int = 32) -> None:
        self.window = window
        self.alphabet = alphabet
        self.max_matches = max_matches  # cap retrievals per query for speed
        # hash_of_K_byte_window -> list of positions where that window started
        self._index: dict[bytes, list[int]] = defaultdict(list)
        # The full prefix we've seen so far (encoder + decoder both keep it)
        self._prefix: bytearray = bytearray()

    # --------------------------------------------------------------------- API

    def predict(self) -> tuple[np.ndarray, int]:
        """
        Return (counts, total) for the byte at the *next* position.
        counts[i] = Laplace-smoothed count for byte i.
        """
        counts = np.full(self.alphabet, self.LAPLACE_K, dtype=np.uint32)

        if len(self._prefix) >= self.window:
            ctx = bytes(self._prefix[-self.window:])
            matches = self._index.get(ctx, [])
            # Take the most-recent up to max_matches
            for pos in matches[-self.max_matches:]:
                follow = pos + self.window
                if follow < len(self._prefix):
                    counts[self._prefix[follow]] += 1

        total = int(counts.sum())
        return counts, total

    def update(self, byte: int) -> None:
        """Append the next byte. Both sides call this in lockstep."""
        self._prefix.append(byte)
        # When the appended byte completes a new K-byte context that ENDS
        # somewhere we haven't indexed yet, register it. We index every
        # K-byte window that starts in the prefix and is fully inside it.
        if len(self._prefix) >= self.window:
            start = len(self._prefix) - self.window
            ctx = bytes(self._prefix[start:start + self.window])
            self._index[ctx].append(start)

    # ---------------------------------------------------------------- helpers

    def stats(self) -> dict:
        unique = len(self._index)
        total_positions = sum(len(v) for v in self._index.values())
        return {
            "prefix_len": len(self._prefix),
            "unique_contexts": unique,
            "total_indexed_positions": total_positions,
            "avg_matches_per_context": total_positions / max(1, unique),
        }


def cum_freqs_from_counts(counts: np.ndarray) -> tuple[list[int], int]:
    """
    Convert a 256-element count array into the cumulative-freq form
    that the arithmetic encoder consumes. Identical layout to MarkovModel.
    """
    cum = [0] * (256 + 1)
    total = 0
    for i in range(256):
        cum[i] = total
        total += int(counts[i])
    cum[256] = total
    return cum, total
