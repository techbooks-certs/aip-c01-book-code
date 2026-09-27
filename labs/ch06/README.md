# Lab 6 — Versioned prompt contract and local workflow

## Start and scope

```bash
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.prompt_flow \
  --asset prompts/ch06/claims-summary-v1.json \
  --claim data/synthetic/claim-0001.json \
  --output artifacts/ch06-prompt.json
```

## Deliberate regression

```bash
PYTHONPATH=src python -m claimsassist.prompt_flow \
  --asset prompts/ch06/claims-summary-v2-regressive.json \
  --claim data/synthetic/claim-0001.json \
  --output artifacts/ch06-rejected.json
```

## Workflow branches and evidence

```bash
python scripts/check_examples.py --output artifacts/ch06-regression.json
```
