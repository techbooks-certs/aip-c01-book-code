# Lab 9 — Versioned safety fixture and adversarial suite

## Executed local observation exercise

```bash
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.hardening_demo --chapter 9 --output artifacts/ch09-observations.json
python scripts/check_examples.py --output artifacts/ch09-regression.json
```
