#!/usr/bin/env bash
# Listing 4.2 [R] — Replace and delete versioned policy evidence
# AWS Generative AI Developer in Practice

mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.index_demo \
  --output artifacts/ch04-index.json
