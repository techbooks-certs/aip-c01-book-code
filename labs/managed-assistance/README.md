# Join retrieval, safety, drafting and audit through one request contract

```bash
mkdir -p artifacts
PYTHONPATH=src python -m claimsassist.managed_assistance --output artifacts/joined-assistance.json
PYTHONPATH=.:src python -m unittest tests.integration.test_managed_assistance -v
```

## Optional managed configuration: explicit prerequisites

```python
import boto3
from botocore.config import Config
from claimsassist.managed_assistance import AssistanceConfig, assist

session = boto3.Session(profile_name=approved_profile, region_name=approved_region)
network = Config(connect_timeout=5, read_timeout=60, retries={"total_max_attempts": 1})
result = assist(
    synthetic_question,
    context=verified_context,
    resource_tenant=claim_record.tenant_id,
    resource_id=claim_record.claim_id,
    config=AssistanceConfig(knowledge_base_id, guardrail_id, published_version,
                            model_id, "draft-v1", authorization_policy_version),
    retrieval_client=session.client("bedrock-agent-runtime", config=network),
    runtime_client=session.client("bedrock-runtime", config=network),
    source_allowed=current_source_allowed,
    audit_sink=persist_audit,
)
```
