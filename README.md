# AWS Generative AI Developer in Practice — Companion Code

Companion examples for **AWS Generative AI Developer in Practice**, by **Edmund Ashcombe**, for the AWS Certified Generative AI Developer – Professional (AIP-C01) certification.

## Using the examples

ClaimsAssist uses fictional claims and policy data. Depending on the example, start with local simulation or connect the relevant components to AWS. Local simulation uses supplied data and simulated responses so you can follow the workflow and explore failure handling before making AWS calls. AWS examples require your own authorized account, credentials, compatible resources and model configuration, and can incur charges. Remove resources created for an activity when they are no longer needed, and check for remaining billable resources.

Run commands from the repository root. Illustrative fragments show an implementation pattern and require adaptation; they are not standalone applications. Keep access keys and real customer data out of this repository.

## Setup — Python 3.12

Use the supplied dependency versions. Do not substitute a different Python major/minor version for this edition.

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = ".;src"
.\.venv\Scripts\python.exe scripts/check_examples.py
.\.venv\Scripts\python.exe -m claimsassist --input data/synthetic/claim-0001.json
```

Activation is not required when using the explicit interpreter path above. For a multiline command printed in shell syntax in the book, remove its `PYTHONPATH=...` prefix, use the PowerShell environment setting above, join continuation lines, and replace `python` or `python3` with `.\.venv\Scripts\python.exe`. Use `New-Item -ItemType Directory -Force artifacts` in place of `mkdir -p artifacts`. For the Python here-document in Listing C.4, paste its Python body into a PowerShell here-string and pipe that string to `.\.venv\Scripts\python.exe -`.

### macOS / Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/check_examples.py
PYTHONPATH=src python -m claimsassist --input data/synthetic/claim-0001.json
```

On Linux x86-64, the optional hash-checked installation is:

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements-agent-linux-py312.lock
```

The `requirements.txt` environment markers select the dependency variant for the current platform. The lock file is specifically for Linux x86-64 / Python 3.12; do not use it as a universal Windows or macOS lock.

## Check your changes

```bash
python scripts/check_examples.py --output artifacts/example-checks.json
```

Use `.\.venv\Scripts\python.exe` instead of `python` on Windows when the environment is not activated. The command reports the actual test count, dependency-version matches, failures, errors and skips. Investigate a nonzero exit code before continuing. Reports and other files under `artifacts/` stay on your computer and are ignored by Git.

Some exercises intentionally return nonzero exit codes: Listing 1.2 returns **2** for the cross-tenant rejection; Listing 3.2 returns **3** when it saves the intended partially quarantined batch. These are expected exercise outcomes, not failed test-suite runs. Use fresh output filenames or directories when an exercise refuses to overwrite an existing result.

## Find an example

| Location | Purpose |
| --- | --- |
| `listings/` | The numbered code bodies from the book, with listing captions. |
| `src/claimsassist/` | Application components required by the exercises. |
| `data/synthetic/`, `config/`, `prompts/` | Fictional inputs and exercise configuration. |
| `labs/` | Chapter-specific commands and brief usage notes. |
| `tests/`, `scripts/check_examples.py` | Checks for the example application. |
| [REFERENCES.md](REFERENCES.md) | Chapter-organized official documentation links. |

### Chapter guides

- [Chapter 1 — Foundations](labs/ch01/README.md)
- [Chapter 2 — Model selection and lifecycle](labs/ch02/README.md)
- [Chapter 3 — Data validation and multimodal processing](labs/ch03/README.md)
- [Chapter 4 — Embeddings and vector stores](labs/ch04/README.md)
- [Chapter 5 — Production RAG](labs/ch05/README.md)
- [Chapter 6 — Prompt contracts and governed releases](labs/ch06/README.md)
- [Chapter 7 — Agents, tools, MCP and memory](labs/ch07/README.md)
- [Chapter 8 — Deployment and enterprise integration](labs/ch08/README.md)
- [Chapter 9 — AI safety](labs/ch09/README.md)
- [Chapter 10 — Security and governance](labs/ch10/README.md)
- [Chapter 11 — Cost and performance](labs/ch11/README.md)
- [Chapter 12 — Observability and troubleshooting](labs/ch12/README.md)
- [Chapter 13 — Evaluation and capstone](labs/ch13/README.md)

Related exercises: [managed assistance](labs/managed-assistance/README.md), [quality monitoring](labs/quality-monitoring/README.md), and the [composed capstone](labs/ch13/composed-journey.md).

### Numbered listings

**R** = runnable after setup; **I** = illustrative fragment or data shape; **O** = sample output. Run shell listings from the repository root, not their chapter directory. Python fragments require the configured clients and variables identified in the surrounding chapter.

| Listing | Type | File |
| --- | --- | --- |
| 1.1 | R | [Run the baseline on a valid synthetic claim](listings/ch01/listing-1-1.sh) |
| 1.2 | R | [Submit another tenant’s claim and observe the rejection](listings/ch01/listing-1-2.sh) |
| 1.3 | R | [Run the example checks](listings/ch01/listing-1-3.sh) |
| 2.1 | R | [Compare model routes offline and simulate a fallback](listings/ch02/listing-2-1.sh) |
| 3.1 | I | [Converse request with an image content block](listings/ch03/listing-3-1.py) |
| 3.2 | R | [Prepare and quarantine a synthetic intake batch](listings/ch03/listing-3-2.sh) |
| 4.1 | I | [Knowledge Bases field mapping for an OpenSearch Serverless vector index](listings/ch04/listing-4-1.json) |
| 4.2 | R | [Replace and delete versioned policy evidence](listings/ch04/listing-4-2.sh) |
| 5.1 | R | [Diagnose the missing parent condition](listings/ch05/listing-5-1.sh) |
| 6.1 | R | [Run the governed prompt workflow](listings/ch06/listing-6-1.sh) |
| 7.1 | R | [Run the agent lab with a new approval ledger](listings/ch07/listing-7-1.sh) |
| 8.1 | I | [Application-owned document-ready event](listings/ch08/listing-8-1.json) |
| 8.2 | I | [Developer-assistant shortcut to reject in review](listings/ch08/listing-8-2.py) |
| 8.3 | R | [Run the local deployment, queue and rollback lab](listings/ch08/listing-8-3.sh) |
| 13.1 | R | [Run the composed capstone journey](listings/ch13/listing-13-1.sh) |
| C.1 | O | [Application streaming event](listings/appendix-c/listing-c-1.json) |
| C.2 | I | [Proposed tool-use input](listings/appendix-c/listing-c-2.json) |
| C.3 | R | [Run the queue and approval boundary tests](listings/appendix-c/listing-c-3.sh) |
| C.4 | R | [Inspect a sanitized telemetry event](listings/appendix-c/listing-c-4.sh) |
| C.5 | I | [ApplyGuardrail request fragment](listings/appendix-c/listing-c-5.py) |
| C.6 | I | [Knowledge Bases Retrieve request fragment](listings/appendix-c/listing-c-6.py) |
| C.7 | I | [Wire the managed adapters](listings/appendix-c/listing-c-7.py) |
| C.8 | R | [Run the example checks](listings/appendix-c/listing-c-8.sh) |

Listing 8.2 deliberately shows an incorrect code-review fragment with a top-level `return`; read it as the rejected pattern, not as a standalone Python program.

## License

The original companion code and its supporting material in this repository are released under the [MIT License](LICENSE), Copyright (c) 2026 Edmund Ashcombe. The book's prose, practice questions, illustrations and cover artwork are not covered by this license. Third-party packages retain their own licenses.

## Windows test prerequisite

The complete check suite creates temporary symbolic links to verify path rejection. On Windows, enable Developer Mode on a device you are permitted to configure, or use an environment where symbolic-link creation is authorized. Run the checks in an ordinary terminal; do not skip the path-security tests. Developer Mode is not required for exercises that do not create symbolic links.
