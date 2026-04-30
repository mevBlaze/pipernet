"""
Bench: compare middle-out v0 baseline against retrieval-augmented mixers.

Usage:
    python3 bench.py [SLICE_BYTES]    # default: 100_000 bytes

Produces honest, reproducible numbers. Every number printed was produced by
this script; if your run differs, your run wins.
"""
from __future__ import annotations

import gzip
import sys
import time
from pathlib import Path

# baseline
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.baseline import encode as baseline_encode, decode as baseline_decode  # noqa: E402

# track-b mixers
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mixer_multi import encode as multi_encode, decode as multi_decode  # noqa: E402


def main(slice_bytes: int = 100_000) -> int:
    enwik = Path("/tmp/enwik8")
    if not enwik.exists():
        print("error: /tmp/enwik8 not found — download it first:", file=sys.stderr)
        print("  curl -O http://mattmahoney.net/dc/enwik8.zip && unzip enwik8.zip && mv enwik8 /tmp/enwik8", file=sys.stderr)
        return 1

    raw = enwik.read_bytes()[:slice_bytes]
    n = len(raw)

    print(f"corpus: enwik8[:{n}]  ({n:_} bytes)")
    print("-" * 70)

    # baseline
    t0 = time.perf_counter()
    baseline_blob = baseline_encode(raw)
    t_base_enc = time.perf_counter() - t0

    t0 = time.perf_counter()
    baseline_back = baseline_decode(baseline_blob)
    t_base_dec = time.perf_counter() - t0
    assert baseline_back == raw, "baseline round-trip failed"

    base_size = len(baseline_blob)
    base_bpb = (base_size * 8) / n

    # track-b v0.3 multi-window mixer (4 match models + Markov)
    t0 = time.perf_counter()
    multi_blob = multi_encode(raw)
    t_multi_enc = time.perf_counter() - t0

    t0 = time.perf_counter()
    multi_back = multi_decode(multi_blob)
    t_multi_dec = time.perf_counter() - t0
    assert multi_back == raw, "multi-mixer round-trip failed"

    multi_size = len(multi_blob)
    multi_bpb = (multi_size * 8) / n

    # Reference: gzip
    gzip_size = len(gzip.compress(raw, compresslevel=9))
    gzip_bpb = (gzip_size * 8) / n

    W = 32
    print(f"{'compressor':<{W}} {'bytes':>10} {'bpb':>8} {'ratio':>8} {'enc(s)':>8} {'dec(s)':>8}")
    print("-" * 70)
    print(f"{'gzip -9':<{W}} {gzip_size:>10_} {gzip_bpb:>8.3f} {n/gzip_size:>8.3f} "
          f"{'-':>8} {'-':>8}")
    print(f"{'middle-out v0 (order-3)':<{W}} {base_size:>10_} {base_bpb:>8.3f} {n/base_size:>8.3f} "
          f"{t_base_enc:>8.2f} {t_base_dec:>8.2f}")
    print(f"{'track-b v0.3 (4-window mix)':<{W}} {multi_size:>10_} {multi_bpb:>8.3f} {n/multi_size:>8.3f} "
          f"{t_multi_enc:>8.2f} {t_multi_dec:>8.2f}")
    print("-" * 70)

    # Architectural lift over baseline
    if multi_size < base_size:
        delta = base_size - multi_size
        pct = 100 * delta / base_size
        gzip_delta_pct = 100 * (multi_size - gzip_size) / gzip_size
        print(f"track-b v0.3 lift: {delta:_} bytes saved ({pct:.2f}% smaller than baseline)")
        if multi_size > gzip_size:
            print(f"vs gzip: {gzip_delta_pct:.2f}% behind gzip -9")
        else:
            print(f"vs gzip: {-gzip_delta_pct:.2f}% ahead of gzip -9")
    else:
        delta = multi_size - base_size
        pct = 100 * delta / base_size
        print(f"track-b regression: {delta:_} bytes worse than baseline ({pct:.2f}%)")

    return 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    sys.exit(main(n))
