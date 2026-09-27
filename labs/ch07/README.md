# Lab 7 — Bounded Strands agent and approval ledger

## Start and scope

```bash
python3.12 -m venv .venv-agent
.venv-agent/bin/python -m pip install --require-hashes -r requirements-agent-linux-py312.lock
```

## Run the bounded fixture

```bash
mkdir -p artifacts
PYTHONPATH=src .venv-agent/bin/python -m claimsassist.agent_lab \
  --ledger artifacts/ch07-ledger.sqlite \
  --output artifacts/ch07-agent.json
```

## Failure exercises

```bash
.venv-agent/bin/python scripts/check_examples.py --output artifacts/ch07-regression.json
```
