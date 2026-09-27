#!/usr/bin/env bash
# Listing 13.1 [R] — Run the composed capstone journey
# AWS Generative AI Developer in Practice

mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.integrated_capstone \
  --manifest data/synthetic/ch03/manifest.json \
  --sources data/synthetic/ch03 \
  --output-dir artifacts/composed-capstone
