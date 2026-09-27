#!/usr/bin/env bash
# Listing C.3 [R] — Run the queue and approval boundary tests
# AWS Generative AI Developer in Practice

PYTHONPATH=.:src python -m unittest \
  tests.unit.test_deployment \
  tests.integration.test_approval -v
