# Lab 11 — Controlled optimization benchmark

## Executed local observation exercise

```bash
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.hardening_demo --chapter 11 --output artifacts/ch11-observations.json
python scripts/check_examples.py --output artifacts/ch11-regression.json
```

## Execute the baseline and three changed paths

```bash
PYTHONPATH=src python -m claimsassist.optimization_benchmark --output artifacts/ch11-executed-benchmark.json
PYTHONPATH=src python -m unittest tests.integration.test_optimization_benchmark
```

### Optional real-provider comparison

```bash
PYTHONPATH=src python -m claimsassist.optimization_benchmark --live --profile "$LAB_PROFILE" --region "$LAB_REGION" --primary-model "$LAB_PRIMARY_MODEL" --efficient-model "$LAB_EFFICIENT_MODEL" --acknowledge-charges --output artifacts/ch11-live-benchmark.json
```
