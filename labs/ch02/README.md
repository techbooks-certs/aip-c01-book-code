# Lab 2 — Compare model routes without weakening the contract

## Run the offline comparison

```bash
mkdir -p artifacts
PYTHONPATH=src python3 -m claimsassist.compare \
  --config config/ch02-offline.json \
  --cases data/synthetic/ch02-development.json \
  --output artifacts/ch02-comparison.json \
  --simulate-fallback
```

```powershell
New-Item -ItemType Directory -Force artifacts | Out-Null
$env:PYTHONPATH = "src"
python -m claimsassist.compare --config config/ch02-offline.json --cases data/synthetic/ch02-development.json --output artifacts/ch02-comparison.json --simulate-fallback
```

## Validate the implementation

```bash
python scripts/check_examples.py --output artifacts/ch02-regression.json
```

## Optional AWS comparison — charges apply

```bash
PYTHONPATH=src python3 -m claimsassist.compare \
  --config artifacts/ch02-approved-live.json \
  --cases data/synthetic/ch02-development.json \
  --output artifacts/ch02-live-observations.json \
  --live --profile YOUR_APPROVED_PROFILE --acknowledge-charges
```
