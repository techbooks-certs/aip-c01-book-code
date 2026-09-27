# Lab 13 — Integrate ClaimsAssist and challenge its release evidence

## Run the integrated exercise

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements-agent-linux-py312.lock
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.capstone --output artifacts/capstone.json
python scripts/check_examples.py --output artifacts/release-verification.json
```

## Challenge the boundaries

```bash
PYTHONPATH=.:src python -m unittest tests.integration.test_capstone -v
```

## Verify the evidence files

```json
{
  "architecture_decision": {"path": "architecture.md", "sha256": "<64 lowercase hex characters>"},
  "release_manifest": {"path": "release.json", "sha256": "<digest>"},
  "dataset_manifest": {"path": "dataset.json", "sha256": "<digest>"},
  "evaluation_report": {"path": "evaluation.json", "sha256": "<digest>"},
  "threat_control_review": {"path": "controls.md", "sha256": "<digest>"},
  "cost_assumptions": {"path": "cost.md", "sha256": "<digest>"},
  "runbook": {"path": "runbook.md", "sha256": "<digest>"},
  "cleanup_verification": {"path": "cleanup.json", "sha256": "<digest>"}
}
```

```python
import json
from pathlib import Path
from claimsassist.evidence import verify_evidence_files

root = Path("artifacts/evidence-pack")
manifest = json.loads((root / "manifest.json").read_text())
report = verify_evidence_files(root, manifest)
print(report["integrity_verified"], report["content_approved"])
```

## Executed local observation exercise

```bash
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.hardening_demo --chapter 13 --output artifacts/ch13-observations.json
python scripts/check_examples.py --output artifacts/ch13-regression.json
```
