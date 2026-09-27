#!/usr/bin/env bash
# Listing 6.1 [R] — Run the governed prompt workflow
# AWS Generative AI Developer in Practice

mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.prompt_flow \
  --asset prompts/ch06/claims-summary-v1.json \
  --claim data/synthetic/claim-0001.json \
  --output artifacts/ch06-prompt.json
