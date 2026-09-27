import json
from pathlib import Path
import unittest

try:
    from botocore.session import Session
    from botocore.validate import validate_parameters
    from botocore.exceptions import ParamValidationError

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
from claimsassist.baseline import Claim, ModelConfig, build_request
from claimsassist.prompt_flow import prepare

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(SDK_AVAILABLE, "Install the pinned agent dependency lock")
class SDKShapeTests(unittest.TestCase):
    def setUp(self):
        self.shape = (
            Session()
            .get_service_model("bedrock-runtime")
            .operation_model("Converse")
            .input_shape
        )
        self.claim = json.loads((ROOT / "data/synthetic/claim-0001.json").read_text())

    def test_baseline_request_matches_installed_converse_schema(self):
        request = build_request(
            Claim.from_mapping(self.claim), ModelConfig("fixture-converse")
        )
        validate_parameters(request, self.shape)

    def test_prompt_workflow_request_matches_installed_schema(self):
        asset = json.loads((ROOT / "prompts/ch06/claims-summary-v1.json").read_text())
        evidence = dict(
            tenant_id=self.claim["tenant_id"],
            items=[dict(source_id="policy", quote="Synthetic evidence.")],
        )
        request, _ = prepare(asset, self.claim, evidence, self.claim["tenant_id"])
        validate_parameters(request, self.shape)

    def test_unknown_request_field_is_rejected_by_sdk(self):
        request = build_request(
            Claim.from_mapping(self.claim), ModelConfig("fixture-converse")
        )
        request["unsupportedClaimsField"] = True
        with self.assertRaises(ParamValidationError):
            validate_parameters(request, self.shape)
