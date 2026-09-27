#!/usr/bin/env bash
# Listing 2.1 [R] — Compare model routes offline and simulate a fallback
# AWS Generative AI Developer in Practice

mkdir -p artifacts
PYTHONPATH=src python3 -m claimsassist.compare \
  --config config/ch02-offline.json \
  --cases data/synthetic/ch02-development.json \
  --output artifacts/ch02-comparison.json \
  --simulate-fallback
