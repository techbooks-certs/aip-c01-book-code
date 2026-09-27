#!/usr/bin/env bash
# Listing 8.3 [R] — Run the local deployment, queue and rollback lab
# AWS Generative AI Developer in Practice

mkdir -p artifacts/ch08
PYTHONPATH=src python -m claimsassist.deployment_cli \
  --directory artifacts/ch08 \
  --output artifacts/ch08/report.json
