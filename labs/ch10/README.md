# Lab 10 — Tenant isolation, safe logs and audit evidence

## Executed local observation exercise

```bash
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.hardening_demo --chapter 10 --output artifacts/ch10-observations.json
python scripts/check_examples.py --output artifacts/ch10-regression.json
```
