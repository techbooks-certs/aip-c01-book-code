# Lab 8 — Offline deployment contract, queue idempotency and release rollback

## Run the fixture

```bash
mkdir -p artifacts/ch08
PYTHONPATH=src python -m claimsassist.deployment_cli \
  --directory artifacts/ch08 \
  --output artifacts/ch08/report.json
```

## Failure exercises

```bash
python scripts/check_examples.py --output artifacts/ch08-regression.json
```

## Run the runtime application locally

```bash
PYTHONPATH=src python -m uvicorn claimsassist.runtime_app:app --host 127.0.0.1 --port 8080 --no-access-log --limit-concurrency 8
```

```bash
curl --fail http://127.0.0.1:8080/ping
curl --fail -H 'Content-Type: application/json' -d '{"prompt":"Explain the fictional corrosion exclusion."}' http://127.0.0.1:8080/invocations
curl --no-buffer --fail -H 'Content-Type: application/json' -H 'Accept: text/event-stream' -d '{"prompt":"Explain the fictional corrosion exclusion."}' http://127.0.0.1:8080/invocations
```
