# Listing 8.2 [I] — Developer-assistant shortcut to reject in review
# AWS Generative AI Developer in Practice

# Proposed shortcut — reject in review.
model_id = request.get("model_id", configured_model)
return {"answer": run_model(model_id, request["prompt"]),
        "release_id": configured_release}
