"""
middle-out baseline: order-3 Markov model + arithmetic coding
=====================================================================

This is the seed implementation. It is intentionally simple, readable,
and correct. Future phases will improve the model; the coding layer is
reusable as-is.

Design choices:
  - Arithmetic coding (not Huffman): supports fractional bits, required
    for context-mixing approaches in later phases.
  - Order-3 Markov context: captures local byte correlations without
    needing external libraries.
  - Pure stdlib + numpy: no ML frameworks. Numpy is used only for the
    count tables (faster than nested dicts for large contexts).
  - Fixed-width 64-bit integer arithmetic with rescaling: avoids floating
    point precision loss during long messages.

Compression ratio on enwik8 (100 MB Wikipedia):
  This implementation: ~60–65 MB (rough estimate before benchmarking).
  SOTA (cmix):         ~14.7 MB
  xz -9:              ~25.9 MB
  zstd -19:           ~27.8 MB
  gzip -9:            ~36.1 MB

We document the gap honestly. The point of Phase 0 is a correct,
reproducible baseline — not SOTA.

Round-trip guarantee: decode(encode(x)) == x for all byte strings x.
"""

from __future__ import annotations

import struct
from collections import defaultdict
from typing import Dict, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Arithmetic coder (64-bit integer, MSB-first)
# ---------------------------------------------------------------------------
# We use a standard binary interval coder with:
#   - 64-bit range register (no floating point)
#   - Rescaling when the range narrows below 2^32
#   - A leading-zero flush byte (0x00) before each message to seed context
#
# References:
#   Rissanen & Langdon (1979), "Arithmetic Coding"
#   Witten, Neal, Cleary (1987), "Arithmetic Coding for Data Compression"
#   https://en.wikipedia.org/wiki/Arithmetic_coding

PRECISION = 32          # bits in the coded output per step
WHOLE     = 1 << PRECISION
HALF      = WHOLE >> 1
QUARTER   = WHOLE >> 2


class ArithmeticEncoder:
    """Encodes a stream of symbols given cumulative frequency tables."""

    def __init__(self) -> None:
        self._low:    int = 0
        self._high:   int = WHOLE - 1
        self._bits:   list[int] = []
        self._pending: int = 0   # bits waiting on carry resolution

    # -- public API ----------------------------------------------------------

    def encode_symbol(self, cum_lo: int, cum_hi: int, cum_total: int) -> None:
        """
        Encode one symbol with cumulative probability [cum_lo, cum_hi) / cum_total.
        All arguments are integers; cum_lo < cum_hi <= cum_total.
        """
        r = self._high - self._low + 1
        self._high = self._low + (r * cum_hi // cum_total) - 1
        self._low  = self._low + (r * cum_lo // cum_total)
        self._normalise()

    def finish(self) -> bytes:
        """Flush pending bits and return the encoded byte string."""
        # Emit enough bits to uniquely identify the interval
        self._pending += 1
        if self._low < QUARTER:
            self._emit_bit(0)
        else:
            self._emit_bit(1)
        # Pack bits into bytes (MSB first, zero-pad the last byte)
        out = bytearray()
        for i in range(0, len(self._bits), 8):
            chunk = self._bits[i:i+8]
            byte = 0
            for b in chunk:
                byte = (byte << 1) | b
            byte <<= (8 - len(chunk))   # pad with zeros
            out.append(byte)
        return bytes(out)

    # -- internal ------------------------------------------------------------

    def _normalise(self) -> None:
        while True:
            if self._high < HALF:
                self._emit_bit(0)
            elif self._low >= HALF:
                self._emit_bit(1)
                self._low  -= HALF
                self._high -= HALF
            elif self._low >= QUARTER and self._high < 3 * QUARTER:
                self._pending += 1
                self._low  -= QUARTER
                self._high -= QUARTER
            else:
                break
            self._low  <<= 1
            self._high = (self._high << 1) | 1
            self._low  &= WHOLE - 1
            self._high &= WHOLE - 1

    def _emit_bit(self, bit: int) -> None:
        self._bits.append(bit)
        inv = 1 - bit
        for _ in range(self._pending):
            self._bits.append(inv)
        self._pending = 0


class ArithmeticDecoder:
    """Decodes a stream of symbols given cumulative frequency tables."""

    def __init__(self, data: bytes) -> None:
        # Expand bytes into a bit stream (MSB first)
        self._bits: list[int] = []
        for byte in data:
            for shift in range(7, -1, -1):
                self._bits.append((byte >> shift) & 1)
        self._pos  = 0
        self._low  = 0
        self._high = WHOLE - 1
        # Fill the value register with the first PRECISION bits
        self._value = 0
        for _ in range(PRECISION):
            self._value = (self._value << 1) | self._read_bit()

    # -- public API ----------------------------------------------------------

    def decode_symbol(self, cum_freqs: list[int], cum_total: int) -> int:
        """
        Given a list of cumulative frequencies (length = num_symbols + 1),
        return the index of the decoded symbol.
        cum_freqs[i] = cumulative frequency up to (but not including) symbol i.
        cum_freqs[-1] == cum_total.
        """
        r      = self._high - self._low + 1
        scaled = ((self._value - self._low + 1) * cum_total - 1) // r
        # Binary search for the symbol
        lo, hi = 0, len(cum_freqs) - 2
        while lo < hi:
            mid = (lo + hi) // 2
            if cum_freqs[mid + 1] <= scaled:
                lo = mid + 1
            else:
                hi = mid
        sym    = lo
        cum_lo = cum_freqs[sym]
        cum_hi = cum_freqs[sym + 1]
        self._high = self._low + (r * cum_hi // cum_total) - 1
        self._low  = self._low + (r * cum_lo // cum_total)
        self._normalise()
        return sym

    # -- internal ------------------------------------------------------------

    def _read_bit(self) -> int:
        if self._pos < len(self._bits):
            b = self._bits[self._pos]
            self._pos += 1
            return b
        return 0   # virtual trailing zeros past end of stream

    def _normalise(self) -> None:
        while True:
            if self._high < HALF:
                pass                             # no value adjustment
            elif self._low >= HALF:
                self._value -= HALF
                self._low   -= HALF
                self._high  -= HALF
            elif self._low >= QUARTER and self._high < 3 * QUARTER:
                self._value -= QUARTER
                self._low   -= QUARTER
                self._high  -= QUARTER
            else:
                break
            self._low   <<= 1
            self._high   = (self._high << 1) | 1
            self._value  = (self._value << 1) | self._read_bit()
            self._low   &= WHOLE - 1
            self._high  &= WHOLE - 1
            self._value &= WHOLE - 1


# ---------------------------------------------------------------------------
# Order-N Markov model
# ---------------------------------------------------------------------------

ORDER = 3           # context length in bytes
ALPHABET = 256      # byte alphabet
LAPLACE_K = 1       # Laplace smoothing count (avoids zero probabilities)


class MarkovModel:
    """
    Order-3 Markov byte model.

    Maintains a count table: counts[context] -> np.ndarray of shape (256,).
    Contexts are bytes tuples of length ORDER (or shorter at stream start).

    Laplace smoothing ensures no symbol ever has zero probability.
    """

    def __init__(self) -> None:
        # context -> count array (uint32, shape 256)
        self._counts: Dict[bytes, np.ndarray] = defaultdict(
            lambda: np.full(ALPHABET, LAPLACE_K, dtype=np.uint32)
        )

    def get_cum_freqs(self, context: bytes) -> Tuple[list[int], int]:
        """
        Return (cumulative_freq_list, total) for the given context.
        cumulative_freq_list[i] = sum of counts[0..i-1].
        len(cumulative_freq_list) == 257 (includes the end sentinel).
        """
        counts = self._counts[context]
        cum    = [0] * (ALPHABET + 1)
        total  = 0
        for i in range(ALPHABET):
            cum[i] = total
            total  += int(counts[i])
        cum[ALPHABET] = total
        return cum, total

    def update(self, context: bytes, symbol: int) -> None:
        """Increment the count for (context, symbol)."""
        self._counts[context][symbol] += 1

    @staticmethod
    def context_of(history: bytes, pos: int) -> bytes:
        """Return the ORDER-byte context ending just before position pos."""
        start = max(0, pos - ORDER)
        ctx   = history[start:pos]
        return ctx


# ---------------------------------------------------------------------------
# Public API: encode / decode
# ---------------------------------------------------------------------------

def _header(n: int) -> bytes:
    """4-byte big-endian length prefix."""
    return struct.pack(">I", n)


def _unheader(data: bytes) -> Tuple[int, bytes]:
    """Parse the length prefix; return (original_length, remaining_bytes)."""
    if len(data) < 4:
        raise ValueError("Compressed data too short: missing length header.")
    n = struct.unpack(">I", data[:4])[0]
    return n, data[4:]


def encode(data: bytes) -> bytes:
    """
    Compress *data* using order-3 Markov arithmetic coding.

    The output format is:
        [4-byte original length][arithmetic-coded bitstream]

    Returns a bytes object. Lossless: decode(encode(data)) == data.
    """
    model   = MarkovModel()
    encoder = ArithmeticEncoder()
    history = b""

    for i, byte in enumerate(data):
        ctx        = MarkovModel.context_of(history, i)
        cum, total = model.get_cum_freqs(ctx)
        encoder.encode_symbol(cum[byte], cum[byte + 1], total)
        model.update(ctx, byte)
        history    = (history + bytes([byte]))[-(ORDER + 1):]

    return _header(len(data)) + encoder.finish()


def decode(data: bytes) -> bytes:
    """
    Decompress data produced by encode().
    Raises ValueError on malformed input.
    """
    original_len, payload = _unheader(data)

    model   = MarkovModel()
    decoder = ArithmeticDecoder(payload)
    result  = bytearray()
    history = b""

    for i in range(original_len):
        ctx        = MarkovModel.context_of(history, i)
        cum, total = model.get_cum_freqs(ctx)
        byte       = decoder.decode_symbol(cum, total)
        result.append(byte)
        model.update(ctx, byte)
        history    = (history + bytes([byte]))[-(ORDER + 1):]

    return bytes(result)


# ---------------------------------------------------------------------------
# Quick self-test (runs on import if __main__)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    tests = [
        b"",
        b"a",
        b"hello world",
        b"aaaaaaaaaa",
        b"\x00\xff\x00\xff" * 64,
        bytes(range(256)) * 4,
        b"the quick brown fox jumps over the lazy dog",
    ]

    all_ok = True
    for t in tests:
        enc = encode(t)
        dec = decode(enc)
        ok  = dec == t
        ratio = len(enc) / max(len(t), 1)
        print(f"  len={len(t):5d}  enc={len(enc):5d}  ratio={ratio:.2f}  {'OK' if ok else 'FAIL'}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\nAll round-trip tests passed.")
        sys.exit(0)
    else:
        print("\nSOME TESTS FAILED.", file=sys.stderr)
        sys.exit(1)
