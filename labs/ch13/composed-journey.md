# Composed continuation — intake, cited drafting, review and rollback

This continuation executes one synthetic local journey across Chapters 3, 5, 7, 9–10, 12 and 13. The earlier `capstone.py` exercise remains useful for exact-extract approval mechanics. This continuation actually consumes Chapter 3's accepted intake record and calls the joined `managed_assistance` boundary; it does not reconstruct an unrelated claim by hand.

Use the installed edition Python environment from the repository root. No account, credentials, cloud resources or paid model are required:

```bash
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.integrated_capstone \
  --manifest data/synthetic/ch03/manifest.json \
  --sources data/synthetic/ch03 \
  --output-dir artifacts/composed-capstone
PYTHONPATH=.:src python -m unittest tests.integration.test_integrated_capstone -v
```

Choose a new output directory for each run. The command refuses to overwrite an existing directory. The corpus uses synthetic data, including one deliberately named contact-mask example. This is not an OCR, general PII detection or production insurance exercise.

## Observe the executed chain

1. `process_batch` reads source bytes, checks their manifest hashes and parses the form. The accepted `claim-0101` record retains source digest, normalized claim, loss date and policy number. Quarantined entries do not become the chosen claim. The transcript with an unknown date does not substitute for that claim.
2. `draft_prepared` validates the accepted record, authorizes its tenant/role and resolves its policy against the separate synthetic authority catalog. The actual masked description, policy number and loss date enter the question supplied to retrieval and drafting. Inspect `retrieval_queries` in `journey.json` against `intake.json`; this saved detailed content is synthetic exercise evidence, not the operational audit format.
3. The joined boundary executes input assessment, permitted retrieval, document assessment, bounded cited drafting, output assessment, current permission checks and audit-before-return. Model and safety outputs are authored SDK-shaped fixtures. The policy source is the separate fictional `PL-000042` clause; claim intake does not become authoritative policy wording.
4. Baseline and candidate preflight runs establish one local contract case each. The local release registry records actual code/data/evidence digests and the supplied configuration. Its PASS result applies to this example contract case; quality and security assessment require their own checks. Configuration records let the reviewer resolve the audit's configuration digest. Every audit envelope carries the release ID and every request shares the journey correlation ID; unique event IDs distinguish observations.
5. After candidate promotion, inject a fabricated citation. Observe `failure_boundary=response`, withheld answer/evidence, a WITHHELD audit, and `failed_staging_rejected=true`. No proposal is created for this failed draft.
6. Roll back the registry, read its active release and actually run the same accepted intake through that release's configured dependencies. `rollback_behavior_verified=true` requires both the restored baseline identity and matching answer/evidence behavior. This demonstrates local selection and behavior, not an AWS traffic-controller rollback.
7. `stage_proposal` creates only a pending review packet. Its hash covers the intake identity, cited draft, evidence, correlation and release. The approval payload additionally binds source version, policy version and permission epoch. A separate simulated reviewer invokes approval; the model path cannot do so. Execution before approval fails.
8. Change the permission epoch after approval. Execution rejects the stale context. The exercise obtains a fresh draft and new separately approved proposal under the changed epoch; it never resets the epoch to bypass the rejection. Reopen the SQLite ledger, record one simulated receipt and verify replay returns that same receipt.

Inspect `pending_execution_rejected`, `changed_authority_rejected`, `replay_identical` and the returned `simulation_only` receipt. This records local state only: there is no email, payment, claim-status mutation or external business tool. Simulated adjuster identities and clocks are explicit inputs, not authentication evidence.

## Review the evidence without upgrading its claims

The output includes `journey.json`, actual intake and audit observations, `review-packet.json`, two local SQLite databases, eight evidence-role JSON files, `manifest.json`, and `integrity.json`. The existing eight-file verifier checks exact hashes; `content_approved` remains false. Modify one copied role file and rerun `verify_evidence_files` to observe rejection.

`review-labels.json` identifies the exact answer/evidence hashes and supplies a rubric. `support_verified` and reviewer identity are null, with status `AWAITING_INDEPENDENT_REVIEW`. The fixture copies the complete policy passage into its authored answer, but the program does not infer a semantic reviewer label from citation membership or exact text. A reviewer must inspect applicability, statement support and preservation of conditions, then create a separate attributed review record. Do not turn the fixture expectation into a fabricated human assessment or measured real-model performance. Development cases here are not an untouched holdout.

The report measures local boundary durations where the joined code records them. It makes zero AWS calls, records no AWS prices or real-model latency, and does not measure a safety detector. Keep the large content-bearing exercise report restricted in any future non-synthetic adaptation; the operational `audit.jsonl` contains bounded audit fields and release identifiers instead of claim text.

## Managed dependency map

| Local boundary | Implemented seam | Remaining managed implementation/evidence |
| --- | --- | --- |
| Source preparation | Actual `process_batch` output consumed by `draft_prepared` | S3 intake, extraction job adapters, quarantine persistence and managed ingestion are unimplemented |
| Identity/claim/policy authority | Typed context plus separately injected `SyntheticAuthority` | Real IdP validation, claim ownership/effective-policy lookup and current permission revisions are unimplemented |
| Retrieval | Injected client with documented `Retrieve` shape and authoritative predicate | Provisioned KB/backend, connector ingestion and a real authoritative source resolver are required |
| Safety and generation | Injected `ApplyGuardrail`/Converse client through existing joined adapter | For AWS use, configure a published guardrail and evaluate the selected model and safety settings. |
| Audit | Synchronous file sink with release envelope; failure withholds output | Durable cloud sink, retention/access policy and telemetry export are unimplemented here |
| Review | Packet fingerprint and SQLite approval contract; separate simulated reviewer | Authenticated reviewer UI/service and cloud ledger are unimplemented; business effects deliberately absent |
| Release | Local registry selects configured dependencies and reruns behavior after rollback | Cloud routing, workload replay and deployment-specific rollback verification are unimplemented |
| Evidence | Actual generated eight-role package with existing integrity verifier | For an AWS implementation, collect independent support labels and evaluate the deployed workflow, including cleanup. |

Credentials would not implement the missing components in this table. The hosted AgentCore and SQS paths remain separate narrower exercises; this continuation does not claim they now host this workflow. Its seams provide explicit places to connect real dependencies without moving authorization into prompts.

## Failure drill and cleanup

The default run injects candidate citation failure and post-approval permission change. Additional tests change the accepted claim after drafting, remove its date, select the wrong policy, revoke its source, fail audit persistence and quarantine the required source. Predict which boundary rejects each case before reading the test.

Retain the exact output directory until review is complete. Its SQLite ledgers, audit and content-bearing synthetic evidence intentionally remain available after the run. Remove only that directory when its retention period ends; the command provisions no cloud resource. This local exercise creates no AWS resources. For an AWS adaptation, remove resources you provision after the activity.
