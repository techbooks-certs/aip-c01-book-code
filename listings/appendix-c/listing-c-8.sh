#!/usr/bin/env bash
# Listing C.8 [R] — Run the example checks
# AWS Generative AI Developer in Practice

mkdir -p artifacts
python scripts/check_examples.py \
  --output artifacts/example-checks.json
