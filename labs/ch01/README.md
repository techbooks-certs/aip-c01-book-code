# Lab 1 — A bounded ClaimsAssist request

## Run the example

```bash
mkdir -p artifacts/ch01
PYTHONPATH=src python3 -m claimsassist \
  --input data/synthetic/claim-0001.json \
  --output artifacts/ch01/offline-baseline.json
```

## Inject a failure

```bash
PYTHONPATH=src python3 -m claimsassist \
  --input data/synthetic/claim-cross-tenant.json
```

## Verify the deeper invariant

```bash
python scripts/check_examples.py --output artifacts/ch01-regression.json
```

## Optional AWS path — charges apply

```bash
PYTHONPATH=src python3 -m claimsassist \
  --input data/synthetic/claim-0001.json \
  --live --acknowledge-charges \
  --profile YOUR_APPROVED_PROFILE \
  --region YOUR_APPROVED_REGION \
  --model-id YOUR_VERIFIED_MODEL_REFERENCE \
  --output artifacts/ch01/live-baseline.json
```
