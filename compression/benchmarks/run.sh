#!/usr/bin/env bash
# benchmarks/run.sh — run all available compressors against enwik8
# Usage: bash benchmarks/run.sh [--enwik9]
# Outputs: comparison table to stdout + appends a row to benchmarks/results.csv
#
# The Hutter Prize counts: (compressor binary) + (decompressor binary) +
# (compressed file). This script measures compressed file size only — the
# binary size overhead must be counted separately for prize submission.
# See https://prize.hutter1.net/hfaq.htm for the full rules.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_DIR="${REPO_DIR}/benchmarks/data"
TMP_DIR="${REPO_DIR}/benchmarks/tmp"
RESULTS_CSV="${SCRIPT_DIR}/results.csv"

# Parse flags
TARGET="enwik8"
for arg in "$@"; do
    case "$arg" in
        --enwik9) TARGET="enwik9" ;;
        *) echo "Unknown flag: $arg" >&2; exit 1 ;;
    esac
done

mkdir -p "${DATA_DIR}" "${TMP_DIR}"

# ---------------------------------------------------------------------------
# 1. Download + verify the target file
# ---------------------------------------------------------------------------

ENWIK8_URL="http://mattmahoney.net/dc/enwik8.zip"
ENWIK8_SHA256="fc54a09c53ff93bb98b038a716d01eb89c83d1e17f0c33b8ae1a07cb7a3f6ccd"

ENWIK9_URL="http://mattmahoney.net/dc/enwik9.zip"
ENWIK9_SHA256="2996a66d9f45d1c37a5c315d82e08b2da34e1c5a3f6f9bfc48def65f95ebf699"

TARGET_PATH="${DATA_DIR}/${TARGET}"

if [[ "${TARGET}" == "enwik8" ]]; then
    ZIP_URL="${ENWIK8_URL}"
    EXPECTED_SHA="${ENWIK8_SHA256}"
    EXPECTED_SIZE=104857600
else
    ZIP_URL="${ENWIK9_URL}"
    EXPECTED_SHA="${ENWIK9_SHA256}"
    EXPECTED_SIZE=1000000000
fi

if [[ ! -f "${TARGET_PATH}" ]]; then
    echo "[download] ${TARGET} not found — fetching from ${ZIP_URL}"
    ZIP_PATH="${DATA_DIR}/${TARGET}.zip"
    if command -v curl &>/dev/null; then
        curl -L --progress-bar -o "${ZIP_PATH}" "${ZIP_URL}"
    elif command -v wget &>/dev/null; then
        wget -O "${ZIP_PATH}" "${ZIP_URL}"
    else
        echo "ERROR: neither curl nor wget found. Please download manually:" >&2
        echo "  ${ZIP_URL} → ${ZIP_PATH}" >&2
        exit 1
    fi
    echo "[verify] checking SHA-256..."
    if command -v sha256sum &>/dev/null; then
        ACTUAL_SHA=$(sha256sum "${ZIP_PATH}" | awk '{print $1}')
    elif command -v shasum &>/dev/null; then
        ACTUAL_SHA=$(shasum -a 256 "${ZIP_PATH}" | awk '{print $1}')
    else
        echo "WARNING: cannot verify hash (sha256sum / shasum not found). Continuing." >&2
        ACTUAL_SHA="${EXPECTED_SHA}"
    fi
    if [[ "${ACTUAL_SHA}" != "${EXPECTED_SHA}" ]]; then
        echo "ERROR: SHA-256 mismatch for ${ZIP_PATH}" >&2
        echo "  expected: ${EXPECTED_SHA}" >&2
        echo "  actual:   ${ACTUAL_SHA}" >&2
        exit 1
    fi
    echo "[unzip] extracting..."
    unzip -p "${ZIP_PATH}" > "${TARGET_PATH}"
    rm "${ZIP_PATH}"
fi

# Sanity-check file size
ACTUAL_SIZE=$(wc -c < "${TARGET_PATH}" | tr -d ' ')
if [[ "${ACTUAL_SIZE}" != "${EXPECTED_SIZE}" ]]; then
    echo "ERROR: ${TARGET} has unexpected size ${ACTUAL_SIZE} (expected ${EXPECTED_SIZE})" >&2
    exit 1
fi
echo "[ok] ${TARGET} ready: ${ACTUAL_SIZE} bytes"
echo

# ---------------------------------------------------------------------------
# 2. Helper: time a command, return wall seconds
# ---------------------------------------------------------------------------

run_timed() {
    # Usage: run_timed output_file <cmd...>
    local out="$1"; shift
    local start end elapsed
    start=$(python3 -c "import time; print(time.time())")
    "$@" > "${out}" 2>/dev/null
    end=$(python3 -c "import time; print(time.time())")
    elapsed=$(python3 -c "print(f'{${end} - ${start}:.2f}')")
    echo "${elapsed}"
}

run_timed_shell() {
    # Usage: run_timed_shell output_file 'shell command'
    local out="$1"; shift
    local cmd="$1"
    local start end elapsed
    start=$(python3 -c "import time; print(time.time())")
    eval "${cmd}" > "${out}" 2>/dev/null
    end=$(python3 -c "import time; print(time.time())")
    python3 -c "print(f'{${end} - ${start}:.2f}')"
}

human_bytes() {
    python3 -c "
n = int('$1')
for unit in ['B','KB','MB','GB']:
    if n < 1024 or unit == 'GB':
        print(f'{n/1024**(\"BKMG\".index(unit[0])):.1f} {unit}')
        break
"
}

# ---------------------------------------------------------------------------
# 3. Run each compressor
# ---------------------------------------------------------------------------
# Each entry: NAME | COMPRESS_CMD | DECOMPRESS_CMD (both read stdin, write stdout)
# We skip tools that aren't installed (warn, don't exit).

ORIGINAL_SIZE="${ACTUAL_SIZE}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Results accumulator: array of "name,compressed_size,encode_sec,decode_sec"
declare -a ROWS=()

run_compressor() {
    local name="$1"
    local compress_cmd="$2"
    local decompress_cmd="$3"
    local level_arg="${4:-}"   # optional, for display only

    # Check if the binary exists
    local binary
    binary=$(echo "${compress_cmd}" | awk '{print $1}')
    if ! command -v "${binary}" &>/dev/null; then
        printf "  %-18s  SKIPPED (not installed)\n" "${name}"
        return
    fi

    local compressed="${TMP_DIR}/${name//[ \/]/_}.compressed"
    local decompressed="${TMP_DIR}/${name//[ \/]/_}.decompressed"

    # Encode
    local enc_sec
    enc_sec=$(python3 -c "
import subprocess, time, sys
start = time.perf_counter()
with open('${TARGET_PATH}', 'rb') as fin:
    proc = subprocess.run('${compress_cmd}'.split(), stdin=fin, stdout=open('${compressed}', 'wb'), stderr=subprocess.DEVNULL)
elapsed = time.perf_counter() - start
if proc.returncode != 0:
    sys.exit(1)
print(f'{elapsed:.2f}')
" 2>/dev/null) || { printf "  %-18s  ENCODE FAILED\n" "${name}"; return; }

    local comp_size
    comp_size=$(wc -c < "${compressed}" | tr -d ' ')

    # Decode
    local dec_sec
    dec_sec=$(python3 -c "
import subprocess, time, sys
start = time.perf_counter()
with open('${compressed}', 'rb') as fin:
    proc = subprocess.run('${decompress_cmd}'.split(), stdin=fin, stdout=open('${decompressed}', 'wb'), stderr=subprocess.DEVNULL)
elapsed = time.perf_counter() - start
if proc.returncode != 0:
    sys.exit(1)
print(f'{elapsed:.2f}')
" 2>/dev/null) || { printf "  %-18s  DECODE FAILED\n" "${name}"; return; }

    # Verify round-trip
    if ! cmp -s "${TARGET_PATH}" "${decompressed}"; then
        printf "  %-18s  ROUND-TRIP MISMATCH (BUG)\n" "${name}"
        return
    fi

    local ratio
    ratio=$(python3 -c "print(f'{int(\"${comp_size}\") / int(\"${ORIGINAL_SIZE}\"):.4f}')")

    printf "  %-18s  %10s  ratio=%-6s  enc=%5ss  dec=%5ss\n" \
        "${name}" "$(human_bytes ${comp_size})" "${ratio}" "${enc_sec}" "${dec_sec}"

    ROWS+=("${name},${comp_size},${enc_sec},${dec_sec},${TIMESTAMP}")

    rm -f "${compressed}" "${decompressed}"
}

run_python_compressor() {
    # Runs our Python implementation (slower — warn the user)
    local name="middle-out"
    local compressed="${TMP_DIR}/middle_out.compressed"
    local decompressed="${TMP_DIR}/middle_out.decompressed"

    echo
    echo "  [running middle-out Python impl — this will be slow on 100 MB]"

    local enc_sec
    enc_sec=$(python3 -c "
import time, sys
sys.path.insert(0, '${REPO_DIR}')
from src.baseline import encode
data = open('${TARGET_PATH}', 'rb').read()
start = time.perf_counter()
compressed = encode(data)
elapsed = time.perf_counter() - start
open('${compressed}', 'wb').write(compressed)
print(f'{elapsed:.1f}')
" 2>&1) || { printf "  %-18s  ENCODE FAILED: %s\n" "${name}" "${enc_sec}"; return; }

    local comp_size
    comp_size=$(wc -c < "${compressed}" | tr -d ' ')

    local dec_sec
    dec_sec=$(python3 -c "
import time, sys
sys.path.insert(0, '${REPO_DIR}')
from src.baseline import decode
data = open('${compressed}', 'rb').read()
start = time.perf_counter()
original = decode(data)
elapsed = time.perf_counter() - start
open('${decompressed}', 'wb').write(original)
print(f'{elapsed:.1f}')
" 2>&1) || { printf "  %-18s  DECODE FAILED: %s\n" "${name}" "${dec_sec}"; return; }

    if ! cmp -s "${TARGET_PATH}" "${decompressed}"; then
        printf "  %-18s  ROUND-TRIP MISMATCH (BUG)\n" "${name}"
        return
    fi

    local ratio
    ratio=$(python3 -c "print(f'{int(\"${comp_size}\") / int(\"${ORIGINAL_SIZE}\"):.4f}')")

    printf "  %-18s  %10s  ratio=%-6s  enc=%5ss  dec=%5ss\n" \
        "${name}" "$(human_bytes ${comp_size})" "${ratio}" "${enc_sec}" "${dec_sec}"

    ROWS+=("${name},${comp_size},${enc_sec},${dec_sec},${TIMESTAMP}")

    rm -f "${compressed}" "${decompressed}"
}

echo "=================================================="
echo "  middle-out benchmark — ${TARGET} (${ORIGINAL_SIZE} bytes)"
echo "  $(date -u)"
echo "=================================================="
echo

run_compressor "gzip -9"    "gzip -9 -c"    "gzip -d -c"
run_compressor "bzip2 -9"   "bzip2 -9 -c"   "bzip2 -d -c"
run_compressor "xz -9"      "xz -9 -c"      "xz -d -c"
run_compressor "zstd -19"   "zstd -19 -c"   "zstd -d -c"

echo

# Middle-out Python baseline — gated behind a flag to avoid 30+ minute runs
# by default. Pass --with-python to include it.
for arg in "$@"; do
    if [[ "$arg" == "--with-python" ]]; then
        run_python_compressor
        break
    fi
done

echo
echo "(Pass --with-python to also run the middle-out Python baseline.)"
echo "(Note: the Python impl is O(n) per symbol — very slow on 100 MB.)"
echo

# ---------------------------------------------------------------------------
# 4. Write results to CSV
# ---------------------------------------------------------------------------

if [[ ! -f "${RESULTS_CSV}" ]]; then
    echo "compressor,compressed_bytes,encode_sec,decode_sec,timestamp,target" > "${RESULTS_CSV}"
fi

for row in "${ROWS[@]}"; do
    echo "${row},${TARGET}" >> "${RESULTS_CSV}"
done

echo "Results appended to ${RESULTS_CSV}"
