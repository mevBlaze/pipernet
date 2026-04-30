.PHONY: benchmark test clean

## Run the compression benchmark (downloads enwik8 on first run)
benchmark:
	bash benchmarks/run.sh

## Also run the middle-out Python implementation (very slow on 100 MB)
benchmark-full:
	bash benchmarks/run.sh --with-python

## Run against enwik9 (1 GB, requires ~30 min even for standard tools)
benchmark-enwik9:
	bash benchmarks/run.sh --enwik9

## Quick round-trip self-test for the baseline implementation
test:
	python3 src/baseline.py

## Remove generated files (preserves downloaded data)
clean:
	rm -rf benchmarks/tmp __pycache__ src/__pycache__

## Remove downloaded data too (will re-download on next benchmark run)
clean-data:
	rm -rf benchmarks/data
