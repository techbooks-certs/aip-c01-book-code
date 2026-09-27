# Listing C.6 [I] — Knowledge Bases Retrieve request fragment
# AWS Generative AI Developer in Practice

response = agent_runtime.retrieve(
    knowledgeBaseId="ABCDEFGHIJ",
    retrievalQuery={"text": "Corrosion exclusion"},
    retrievalConfiguration={"vectorSearchConfiguration": {
        "numberOfResults": 5,
        "filter": {"equals": {
            "key": "tenant_id", "value": trusted_tenant
        }},
    }},
)
