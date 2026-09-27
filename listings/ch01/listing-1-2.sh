#!/usr/bin/env bash
# Listing 1.2 [R] — Submit another tenant’s claim and observe the rejection
# AWS Generative AI Developer in Practice

PYTHONPATH=src python3 -m claimsassist \
  --input data/synthetic/claim-cross-tenant.json
