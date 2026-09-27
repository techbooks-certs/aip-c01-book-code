# Listing 3.1 [I] — Converse request with an image content block
# AWS Generative AI Developer in Practice

request = {
    "modelId": model_id,
    "messages": [{
        "role": "user",
        "content": [
            {"text": (
                "Describe visible damage only; "
                "identify uncertainty."
            )},
            {"image": {
                "format": "png",
                "source": {"bytes": image_bytes}
            }},
        ],
    }],
    "inferenceConfig": {"maxTokens": 128},
}
# Requires a configured client and permitted model access.
# runtime.converse(**request) calls AWS and can incur charges.
