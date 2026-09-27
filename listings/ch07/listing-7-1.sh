#!/usr/bin/env bash
# Listing 7.1 [R] — Run the agent lab with a new approval ledger
# AWS Generative AI Developer in Practice

mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.agent_lab \
  --ledger artifacts/ch07-ledger.sqlite \
  --output artifacts/ch07-agent.json
