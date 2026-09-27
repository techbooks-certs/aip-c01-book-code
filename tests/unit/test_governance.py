import unittest

from claimsassist.baseline import BoundaryError
from claimsassist.governance import (
    SecurityContext,
    audit_fingerprint,
    authorize_resource,
    build_audit_record,
    redact_record,
    tenant_cache_key,
    verify_deletion_inventory,
)


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.reader = SecurityContext("tenant-a", "user-7", frozenset({"reader"}))
        self.fields = dict(
            event_id="evt-1",
            occurred_at="2026-09-20T05:00:00Z",
            correlation_id="corr-1",
            tenant_id="tenant-a",
            actor_id="user-7",
            actor_role="reader",
            action="policy.read",
            resource_id="policy-9",
            source_version="src-3",
            model_ref="model-2",
            prompt_version="pr-4",
            policy_version="authz-5",
            decision="ALLOW",
        )

    def test_same_tenant_reader_can_read(self):
        self.assertTrue(authorize_resource(self.reader, "tenant-a", "read"))

    def test_cross_tenant_access_is_denied(self):
        with self.assertRaises(BoundaryError):
            authorize_resource(self.reader, "tenant-b", "read")

    def test_reader_cannot_update(self):
        with self.assertRaises(BoundaryError):
            authorize_resource(self.reader, "tenant-a", "update")

    def test_adjuster_can_update_own_tenant(self):
        context = SecurityContext("tenant-a", "adjuster-2", frozenset({"adjuster"}))
        self.assertTrue(authorize_resource(context, "tenant-a", "update"))

    def test_cache_key_changes_with_tenant(self):
        first = tenant_cache_key(
            "tenant-a",
            "claim-1",
            "src-1",
            "model-1",
            "prompt-1",
            permission_version="authz-1",
        )
        second = tenant_cache_key(
            "tenant-b",
            "claim-1",
            "src-1",
            "model-1",
            "prompt-1",
            permission_version="authz-1",
        )
        self.assertNotEqual(first, second)

    def test_cache_key_changes_with_source_version(self):
        first = tenant_cache_key(
            "tenant-a",
            "claim-1",
            "src-1",
            "model-1",
            "prompt-1",
            permission_version="authz-1",
        )
        second = tenant_cache_key(
            "tenant-a",
            "claim-1",
            "src-2",
            "model-1",
            "prompt-1",
            permission_version="authz-1",
        )
        self.assertNotEqual(first, second)

    def test_redaction_uses_allowlist(self):
        record = redact_record(
            {**self.fields, "prompt": "SSN 111-22-3333", "unknown": "x"}
        )
        self.assertNotIn("prompt", record)
        self.assertNotIn("unknown", record)
        self.assertEqual(record["tenant_id"], "tenant-a")

    def test_audit_record_requires_provenance(self):
        fields = dict(self.fields)
        fields.pop("source_version")
        with self.assertRaises(BoundaryError):
            build_audit_record(**fields)

    def test_audit_fingerprint_detects_change(self):
        first = audit_fingerprint(self.fields)
        changed = dict(self.fields, decision="DENY")
        self.assertNotEqual(first, audit_fingerprint(changed))

    def test_deletion_inventory_reports_incomplete_store(self):
        result = verify_deletion_inventory(
            [
                {
                    "store": "objects",
                    "status": "DELETED",
                    "evidence_ref": "verification-objects",
                },
                {"store": "index", "status": "PENDING"},
            ],
            expected_stores={"objects", "index"},
        )
        self.assertFalse(result["complete"])
        self.assertEqual(result["incomplete_stores"], ["index"])

    def test_deletion_inventory_accepts_policy_retention(self):
        result = verify_deletion_inventory(
            [
                {
                    "store": "objects",
                    "status": "DELETED",
                    "evidence_ref": "verification-objects",
                },
                {
                    "store": "audit",
                    "status": "RETAINED_BY_POLICY",
                    "evidence_ref": "verification-audit",
                    "policy_ref": "retention-v1",
                },
                {
                    "store": "cache",
                    "status": "NOT_FOUND",
                    "evidence_ref": "verification-cache",
                },
            ],
            expected_stores={"objects", "audit", "cache"},
        )
        self.assertTrue(result["complete"])

    def test_all_audit_fields_reject_nested_payloads(self):
        for field in {*self.fields, "approval_id"}:
            with self.subTest(field=field):
                with self.assertRaises(BoundaryError):
                    redact_record({field: {"secret": "PRIVATE_SENTINEL"}})
                with self.assertRaises(BoundaryError):
                    build_audit_record(
                        **dict(self.fields, **{field: {"secret": "PRIVATE_SENTINEL"}})
                    )

    def test_audit_timestamp_is_real_utc_calendar_time(self):
        for value in ("2026-02-30T00:00:00Z", "2026-01-01T25:00:00Z", None, True):
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                build_audit_record(**dict(self.fields, occurred_at=value))

    def test_optional_approval_identifier_is_validated(self):
        self.assertEqual(
            build_audit_record(**dict(self.fields, approval_id="approval-7"))[
                "approval_id"
            ],
            "approval-7",
        )
        for value in ([], 1, "", "a" * 101):
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                build_audit_record(**dict(self.fields, approval_id=value))

    def test_cache_key_changes_with_permission_revision(self):
        args = ("tenant-a", "claim-1", "src-1", "model-1", "prompt-1")
        self.assertNotEqual(
            tenant_cache_key(*args, permission_version="p-1"),
            tenant_cache_key(*args, permission_version="p-2"),
        )
        with self.assertRaises(TypeError):
            tenant_cache_key(*args)

    def test_deletion_missing_store_is_not_complete(self):
        report = verify_deletion_inventory(
            [{"store": "objects", "status": "DELETED", "evidence_ref": "check-1"}],
            expected_stores={"objects", "cache"},
        )
        self.assertFalse(report["complete"])
        self.assertEqual(report["missing_stores"], ["cache"])
        self.assertFalse(report["evidence_verified"])

    def test_deletion_rejects_invalid_and_unsubstantiated_records(self):
        cases = [
            [{}],
            [None],
            [{"store": "x", "status": True}],
            [{"store": "x", "status": "DELETED"}],
            [{"store": "x", "status": "RETAINED_BY_POLICY", "evidence_ref": "e1"}],
            [{"store": "x", "status": "PENDING"}] * 2,
            [{"store": "other", "status": "PENDING"}],
            [{"store": "x", "status": "DELETED", "evidence_ref": {"secret": "S"}}],
        ]
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(BoundaryError):
                verify_deletion_inventory(entries, expected_stores={"x"})

    def test_security_roles_require_immutable_set(self):
        with self.assertRaises(BoundaryError):
            SecurityContext("tenant-a", "actor-1", ["reader"])
