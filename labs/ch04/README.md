# Lab 4 — Versioned policy evidence

## Run

```bash
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.index_demo \
  --output artifacts/ch04-index.json
```

## Introduce and repair a compatibility failure

```bash
PYTHONPATH=src python - <<'PY'
from claimsassist.index_demo import passage
from claimsassist.vector_index import PolicyIndex, SPACE
from claimsassist.baseline import BoundaryError
index = PolicyIndex()
p = passage('tenant-amber', 'water-policy', 'v1', 'water evidence')
index.replace_source('tenant-amber', 'water-policy', [p])
try:
    index.search('tenant-amber', '2026-09-20', (1, 0, 1),
                 'different-model-same-dimensions')
except BoundaryError:
    print('Expected: incompatible embedding space rejected')
else:
    raise AssertionError('The compatibility boundary failed')
assert index.search('tenant-amber', '2026-09-20', (1, 0, 1), SPACE)
print('Compatible toy-vector query succeeded')
PY
```

## Run the regression evidence

```bash
python scripts/check_examples.py --output artifacts/ch04-regression.json
```
