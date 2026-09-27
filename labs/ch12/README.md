# Lab 12 — Find the first broken boundary

## Executed local observation exercise

```bash
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.hardening_demo --chapter 12 --output artifacts/ch12-observations.json
python scripts/check_examples.py --output artifacts/ch12-regression.json
```

## Instrument the connected answer path

```bash
PYTHONPATH=src python -m claimsassist.rag_answer --output artifacts/ch12-observed-answer.json
python -c 'import json; from pathlib import Path; r=json.loads(Path("artifacts/ch12-observed-answer.json").read_text()); print(json.dumps(r["trace"],indent=2))'
```
