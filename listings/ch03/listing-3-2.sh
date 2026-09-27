#!/usr/bin/env bash
# Listing 3.2 [R] — Prepare and quarantine a synthetic intake batch
# AWS Generative AI Developer in Practice

mkdir -p artifacts
PYTHONPATH=src python3 -m claimsassist.intake \
  --manifest data/synthetic/ch03/manifest.json \
  --sources data/synthetic/ch03 \
  --output artifacts/ch03-intake.json
