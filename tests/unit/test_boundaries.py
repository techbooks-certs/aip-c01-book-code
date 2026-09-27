import json
import unittest
from unittest.mock import Mock

from claimsassist.baseline import (
    BoundaryError,
    Claim,
    ModelConfig,
    strict_json,
    run_baseline,
)


def valid_claim():
    return {
        "schema_version": 1,
        "claim_id": "claim-0001",
        "tenant_id": "tenant-amber",
        "description": "SYNTHETIC: water entered a fictional storage room.",
    }


class InputBoundaryTests(unittest.TestCase):
    def test_valid_record_preserves_identifier(self):
        claim = Claim.from_mapping(valid_claim())
        self.assertEqual(claim.claim_id, "claim-0001")

    def test_missing_or_extra_fields_rejected(self):
        for data in ({}, {**valid_claim(), "approved": True}):
            with self.subTest(data=data), self.assertRaises(BoundaryError):
                Claim.from_mapping(data)

    def test_non_object_claims_rejected(self):
        for value in (None, [], "record", 1, True):
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                Claim.from_mapping(value)

    def test_schema_version_requires_exact_integer(self):
        for version in (True, 1.0, "1", 0, 2):
            with self.subTest(version=version), self.assertRaises(BoundaryError):
                Claim.from_mapping({**valid_claim(), "schema_version": version})

    def test_description_limits(self):
        for description in (
            "",
            "  ",
            "x" * 4001,
            None,
            42,
            "contains\x00control",
            "bad\ud800",
        ):
            with (
                self.subTest(description=repr(description)[:30]),
                self.assertRaises(BoundaryError),
            ):
                Claim.from_mapping({**valid_claim(), "description": description})
        self.assertEqual(
            len(
                Claim.from_mapping(
                    {**valid_claim(), "description": "x" * 4000}
                ).description
            ),
            4000,
        )

    def test_bad_identifiers_rejected(self):
        for key, value in (
            ("claim_id", "claim-１２３４"),
            ("claim_id", "claim-0001\n"),
            ("tenant_id", "tenant-amber\n"),
            ("tenant_id", "TENANT-AMBER"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(BoundaryError):
                Claim.from_mapping({**valid_claim(), key: value})

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaisesRegex(BoundaryError, "Duplicate"):
            strict_json('{"tenant_id":"tenant-amber","tenant_id":"tenant-birch"}')

    def test_non_finite_and_broken_json_rejected(self):
        for text in ('{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', "{"):
            with self.subTest(text=text), self.assertRaises(BoundaryError):
                strict_json(text)

    def test_invalid_inputs_never_invoke_provider(self):
        client = Mock()
        for data in ({}, {**valid_claim(), "description": ""}):
            with self.assertRaises(BoundaryError):
                run_baseline(data, "tenant-amber", client, ModelConfig("fixture"))
        client.converse.assert_not_called()

    def test_cross_tenant_rejected_before_provider(self):
        client = Mock()
        with self.assertRaisesRegex(BoundaryError, "tenant scope"):
            run_baseline(valid_claim(), "tenant-birch", client, ModelConfig("fixture"))
        client.converse.assert_not_called()

    def test_config_rejects_invalid_limits(self):
        for limit in (0, 4097, True, 1.5, "512"):
            with self.subTest(limit=limit), self.assertRaises(BoundaryError):
                ModelConfig("fixture", limit)

    def test_config_rejects_unsupported_model_references(self):
        for model in ("", " spaced ", "a\nb", "arn:aws:bedrock:x:y:prompt/example:1"):
            with self.subTest(model=model), self.assertRaises(BoundaryError):
                ModelConfig(model)


if __name__ == "__main__":
    unittest.main()
