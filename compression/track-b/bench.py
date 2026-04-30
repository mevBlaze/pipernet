"""
Bench: compare middle-out v0 baseline against retrieval-augmented mixer.

Usage:
    python3 bench.py [SLICE_BYTES]    # default: 100_000 bytes
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# baseline
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.baseline import encode as baseline_encode, decode as baseline_decode  # noqa: E402

# track-b mixer
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mixer import encode as mix_encode, decode as mix_decode  # noqa: E402


def main(slice_bytes: int = 100_000) -> int:
    enwik = Path("/tmp/enwik8")
    if not enwik.exists():
        print("error: /tmp/enwik8 not found", file=sys.stderr)
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

    # track-b mixer
    t0 = time.perf_counter()
    mix_blob = mix_encode(raw)
    t_mix_enc = time.perf_counter() - t0

    t0 = time.perf_counter()
    mix_back = mix_decode(mix_blob)
    t_mix_dec = time.perf_counter() - t0
    assert mix_back == raw, "mixer round-trip failed"

    mix_size = len(mix_blob)
    mix_bpb = (mix_size * 8) / n

    # Reference: gzip
    import gzip
    gzip_size = len(gzip.compress(raw, compresslevel=9))
    gzip_bpb = (gzip_size * 8) / n

    print(f"{'compressor':<28} {'bytes':>10} {'bpb':>8} {'ratio':>8} {'enc(s)':>8} {'dec(s)':>8}")
    print("-" * 70)
    print(f"{'gzip -9':<28} {gzip_size:>10_} {gzip_bpb:>8.3f} {n/gzip_size:>8.3f} "
          f"{'-':>8} {'-':>8}")
    print(f"{'middle-out v0 (order-3)':<28} {base_size:>10_} {base_bpb:>8.3f} {n/base_size:>8.3f} "
          f"{t_base_enc:>8.2f} {t_base_dec:>8.2f}")
    print(f"{'track-b v0.1 (markov+match)':<28} {mix_size:>10_} {mix_bpb:>8.3f} {n/mix_size:>8.3f} "
          f"{t_mix_enc:>8.2f} {t_mix_dec:>8.2f}")
    print("-" * 70)

    # Architectural lift over baseline
    if mix_size < base_size:
        delta = base_size - mix_size
        pct = 100 * delta / base_size
        print(f"track-b lift: {delta:_} bytes saved ({pct:.2f}% smaller than baseline)")
    else:
        delta = mix_size - base_size
        pct = 100 * delta / base_size
        print(f"track-b regression: {delta:_} bytes worse than baseline ({pct:.2f}%)")

    return 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    sys.exit(main(n))
