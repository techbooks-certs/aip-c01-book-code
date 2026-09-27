# Model deployment and rollback exercise

Classification: design worksheet. Nothing in this file creates or approves a cloud resource. Complete it with actual evidence for a real deployment, or label the whole record fictional for the offline exercise.

| Field | Candidate record | Rollback record |
| --- | --- | --- |
| Task and output contract | Specify exact task and contract version | Confirm compatibility with the retained application |
| Base model/version | Exact supported reference | Exact retained reference and lifecycle status |
| Adaptation/artifact identity | Artifact hash and base compatibility | Retained immutable artifact identity |
| Serving runtime/tokenizer | Tested versions and required loading method | Compatible retained versions |
| Model registry/package version | Real registered version, if applicable | Real prior version, if applicable |
| Source and processing destinations | Verified resource metadata and policy evidence | Re-verified eligible route |
| Prompt/adapter/configuration | Immutable source references | Complete compatible dependency bundle |
| Validation | Contract, quality and authorized endpoint evidence | Rehearsed recovery evidence |
| Promotion criteria | Declare before viewing candidate results | Trigger and decision owner |
| Retirement | Inventory, retention and billing ownership | Retain until the justified stabilization window ends |

Exercise A: The candidate scores better on readability but invents a loss year in one case. Decide whether its aggregate score is enough to promote it under the lab rubric. Explain which gate applies.

Exercise B: The new adapter accepts a field named `decision`; the old model returns `summary`. A rollback switches only the model identifier. Identify the incompatible dependency and specify the bundle that must be restored.

Exercise C: The old model has reached a lifecycle state that makes it unavailable to the account. Explain why retaining its identifier in a file is insufficient for rollback and select a separately validated recovery option.

Expected reasoning: A fails the fidelity gate even if prose improves. B requires the compatible prompt, adapter, schema and configuration with the model. C requires a resource that is actually available and authorized, not merely a historical reference. All three decisions need evidence before a real deployment.
