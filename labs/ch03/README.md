# Lab 3 — Validate synthetic documents and a prepared transcript

## Run and inspect

```bash
mkdir -p artifacts
PYTHONPATH=src python3 -m claimsassist.intake \
  --manifest data/synthetic/ch03/manifest.json \
  --sources data/synthetic/ch03 \
  --output artifacts/ch03-intake.json
```

## Evidence

```bash
python scripts/check_examples.py --output artifacts/ch03-regression.json
```
