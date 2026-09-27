# Lab 5 — Retrieval evidence before generated prose

## Start and cost

```bash
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.rag_demo \
  --output artifacts/ch05-rag.json
```

## Failure and repair

```bash
PYTHONPATH=src python - <<'PY'
from claimsassist.rag_demo import build_index
from claimsassist.retrieval import retrieve
small = retrieve(build_index(), 'tenant-amber', '2026-09-20',
                 'water damage', max_chars=2)
assert small['reason'] == 'context_budget_insufficient'
assert small['evidence'] == []
large = retrieve(build_index(), 'tenant-amber', '2026-09-20',
                 'water damage', max_chars=1200)
assert large['status'] == 'evidence_available'
assert all('citation_id' in p for p in large['evidence'])
print('Budget failure and whole-passage repair observed')
PY
```

## Verify

```bash
python scripts/check_examples.py --output artifacts/ch05-regression.json
```

## Continue from evidence to a cited draft

```bash
PYTHONPATH=src python -m claimsassist.rag_answer \
  --output artifacts/ch05-cited-draft.json
PYTHONPATH=.:src python -m unittest tests.integration.test_rag_answer -v
```

### Optional AWS generation — charges apply

```bash
PYTHONPATH=src python -m claimsassist.rag_answer \
  --live --acknowledge-charges \
  --profile YOUR_APPROVED_PROFILE --region YOUR_APPROVED_REGION \
  --model-id YOUR_VERIFIED_MODEL_REFERENCE \
  --output artifacts/ch05-live-cited-draft.json
```
