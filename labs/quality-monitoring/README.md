# Lab — Reviewed quality windows, safe metric publication and a guardrail candidate

## Run the local path

```bash
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.quality_monitoring --record data/synthetic/quality/reviewed-window.json --manifest data/synthetic/quality/case-labels.json --output artifacts/quality-monitoring.json
PYTHONPATH=.:src python -m unittest tests.integration.test_quality_monitoring -v
```

## Optional live metric submission

```bash
PYTHONPATH=src python -m claimsassist.quality_monitoring --record data/synthetic/quality/reviewed-window.json --manifest data/synthetic/quality/case-labels.json --output artifacts/quality-live-attempt.json --live --profile "$LAB_PROFILE" --region "$LAB_REGION" --account "$LAB_ACCOUNT" --ledger artifacts/quality-publication.sqlite --acknowledge-charges
```
