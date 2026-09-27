#!/usr/bin/env bash
# Listing 5.1 [R] — Diagnose the missing parent condition
# AWS Generative AI Developer in Practice

mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.rag_demo \
  --output artifacts/ch05-rag.json
