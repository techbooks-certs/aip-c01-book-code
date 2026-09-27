"""SDK shape and worksheet drift checks; no clients or network calls."""

import base64
from copy import deepcopy
import unittest
from botocore.session import Session
from botocore.validate import validate_parameters
from botocore.exceptions import ParamValidationError
from claimsassist.baseline import BoundaryError
from claimsassist.configuration_examples import (
    image_request,
    backend_contract,
    validate_backend_contract,
)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


class ConfigurationExamples(unittest.TestCase):
    def test_image_message_matches_pinned_converse_shape(self):
        request = image_request("synthetic-vision-model", PNG)
        shape = (
            Session()
            .get_service_model("bedrock-runtime")
            .operation_model("Converse")
            .input_shape
        )
        validate_parameters(request, shape)
        self.assertIs(
            request["messages"][0]["content"][1]["image"]["source"]["bytes"], PNG
        )
        self.assertEqual(request["messages"][0]["role"], "user")

    def test_local_filename_is_not_image_bytes(self):
        with self.assertRaises(BoundaryError):
            image_request("synthetic-vision-model", "claim.png")

    def test_image_budget_is_application_limit(self):
        with self.assertRaises(BoundaryError):
            image_request("synthetic-vision-model", PNG + b"x" * 100_000)

    def test_sdk_rejects_path_member(self):
        request = image_request("synthetic-vision-model", PNG)
        request["messages"][0]["content"][1]["image"]["source"] = {"path": "claim.png"}
        shape = (
            Session()
            .get_service_model("bedrock-runtime")
            .operation_model("Converse")
            .input_shape
        )
        with self.assertRaises(ParamValidationError):
            validate_parameters(request, shape)

    def test_backend_field_mapping_matches_pinned_sdk(self):
        mapping = validate_backend_contract(
            backend_contract(), embedding_space="synthetic-e1-1024", dimensions=1024
        )
        shape = (
            Session()
            .get_service_model("bedrock-agent")
            .shape_for("OpenSearchServerlessFieldMapping")
        )
        validate_parameters(mapping, shape)

    def test_backend_rejects_representation_and_field_drift(self):
        baseline = backend_contract()
        changes = [
            lambda c: c.update(dimensions=512),
            lambda c: c.update(embedding_space="different-same-dimensions"),
            lambda c: c["fieldMapping"].update(textField="missing"),
            lambda c: c["fields"]["policy_text"].update(filterable=False),
            lambda c: c.update(required_source_metadata=["source_id"]),
            lambda c: c["fieldMapping"].update(metadataField="policy_text"),
        ]
        for i, mutate in enumerate(changes):
            with self.subTest(mutation=i):
                contract = deepcopy(baseline)
                mutate(contract)
                with self.assertRaises(BoundaryError):
                    validate_backend_contract(
                        contract, embedding_space="synthetic-e1-1024", dimensions=1024
                    )
        self.assertEqual(baseline, backend_contract())
