#!/usr/bin/env bash
# Listing 1.1 [R] — Run the baseline on a valid synthetic claim
# AWS Generative AI Developer in Practice

PYTHONPATH=src python3 -m claimsassist \
  --input data/synthetic/claim-0001.json
