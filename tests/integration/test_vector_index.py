import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from claimsassist.baseline import BoundaryError
from claimsassist.index_demo import demonstrate, passage
from claimsassist.vector_index import PolicyIndex, SPACE, fixture_embedding, vector


class VectorIndexTests(unittest.TestCase):
    def setUp(self):
        self.index = PolicyIndex()
        self.old = passage("tenant-amber", "policy", "v1", "water evidence")
        self.index.replace_source("tenant-amber", "policy", [self.old])

    def search(self, **kwargs):
        args = dict(
            trusted_tenant="tenant-amber",
            loss_date="2026-09-20",
            query_vector=(1, 0, 1),
            embedding_space=SPACE,
        )
        args.update(kwargs)
        return self.index.search(**args)

    def test_effective_date_boundary(self):
        new = replace(self.old, version="v2", valid_from="2026-09-01")
        self.index.replace_source(
            "tenant-amber", "policy", [replace(self.old, valid_until="2026-09-01"), new]
        )
        self.assertEqual(self.search(loss_date="2026-08-31")[0]["version"], "v1")
        self.assertEqual(self.search(loss_date="2026-09-01")[0]["version"], "v2")
        self.assertEqual(self.search(loss_date="2025-12-31"), [])

    def test_tenant_filter_precedes_limit(self):
        other = replace(self.old, tenant="tenant-birch", source_id="aaa")
        self.index.replace_source("tenant-birch", "aaa", [other])
        self.assertEqual(self.search(top_k=1)[0]["source_id"], "policy")
        self.assertEqual(len(self.search()), 1)

    def test_revocation(self):
        self.index.replace_source(
            "tenant-amber", "policy", [replace(self.old, allowed=False)]
        )
        self.assertEqual(self.search(), [])

    def test_delete_is_scoped_and_idempotent(self):
        other = replace(self.old, tenant="tenant-birch")
        self.index.replace_source("tenant-birch", "policy", [other])
        self.assertEqual(self.index.delete_source("tenant-amber", "policy"), 1)
        self.assertEqual(self.index.delete_source("tenant-amber", "policy"), 0)
        self.assertEqual(len(self.search(trusted_tenant="tenant-birch")), 1)

    def test_invalid_replacement_preserves_old_records(self):
        for candidate in [
            replace(self.old, tenant="tenant-birch"),
            replace(self.old, source_id="other"),
            replace(self.old, embedding_space="different-model-same-dimensions"),
        ]:
            with self.subTest(candidate=candidate), self.assertRaises(BoundaryError):
                self.index.replace_source("tenant-amber", "policy", [candidate])
            self.assertEqual(self.search()[0]["version"], "v1")

    def test_overlap_rejected(self):
        with self.assertRaises(BoundaryError):
            self.index.replace_source(
                "tenant-amber",
                "policy",
                [self.old, replace(self.old, version="v2", valid_from="2026-09-01")],
            )
        self.assertEqual(self.index.count, 1)

    def test_inconsistent_version_dates_rejected(self):
        with self.assertRaises(BoundaryError):
            self.index.replace_source(
                "tenant-amber",
                "policy",
                [
                    self.old,
                    replace(self.old, chunk_id="clause-2", valid_from="2026-02-01"),
                ],
            )

    def test_duplicate_identity_rejected(self):
        with self.assertRaises(BoundaryError):
            self.index.replace_source("tenant-amber", "policy", [self.old, self.old])

    def test_replacement_removes_stale_chunks(self):
        self.index.replace_source(
            "tenant-amber", "policy", [self.old, replace(self.old, chunk_id="clause-2")]
        )
        self.assertEqual(self.index.count, 2)
        self.index.replace_source("tenant-amber", "policy", [self.old])
        self.assertEqual(self.index.count, 1)

    def test_query_space_mismatch(self):
        with self.assertRaises(BoundaryError):
            self.search(embedding_space="same-size-other-space")

    def test_invalid_vectors(self):
        for value in [
            (0, 0, 0),
            (1, 2),
            (True, 0, 1),
            (float("nan"), 0, 1),
            (float("inf"), 0, 1),
            (1000001, 0, 0),
        ]:
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                vector(value)

    def test_invalid_dates_and_limits(self):
        for value in ["2026-02-30", "2026-2-01", None]:
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                self.search(loss_date=value)
        for value in [True, 0, 21, 1.5]:
            with self.subTest(value=value), self.assertRaises(BoundaryError):
                self.search(top_k=value)

    def test_invalid_passage_metadata(self):
        for changes in [
            dict(valid_until="2026-01-01"),
            dict(allowed=1),
            dict(tenant="untrusted"),
            dict(text=""),
        ]:
            with self.subTest(changes=changes), self.assertRaises(BoundaryError):
                replace(self.old, **changes)

    def test_embedding_input_copied(self):
        values = [1, 0, 0]
        item = replace(self.old, embedding=values)
        values[0] = 0
        self.assertEqual(item.embedding, (1.0, 0.0, 0.0))

    def test_no_keyword_is_explicit_error(self):
        with self.assertRaises(BoundaryError):
            fixture_embedding("unrepresented vocabulary")

    def test_citation_and_stable_ties(self):
        self.index.replace_source(
            "tenant-amber", "policy", [replace(self.old, chunk_id="clause-2"), self.old]
        )
        result = self.search()
        self.assertEqual([r["chunk_id"] for r in result], ["clause-1", "clause-2"])
        self.assertEqual(result[0]["location"], "synthetic policy clause 1")
        self.assertEqual(len(result[0]["text_sha256"]), 64)

    def test_demonstration_observations(self):
        result = demonstrate()
        self.assertEqual(result["evidence_class"], "offline_fixture_vectors")
        self.assertEqual(result["deleted_passages"], 1)
        self.assertEqual([r["version"] for r in result["after_delete"]], ["v2"])
        self.assertEqual(
            [
                r["version"]
                for r in result["historical"]
                if r["source_id"] == "water-policy"
            ],
            ["v1"],
        )
        self.assertEqual(len(result["other_tenant"]), 1)

    def test_cli_output_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            command = [
                sys.executable,
                "-m",
                "claimsassist.index_demo",
                "--output",
                str(path),
            ]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            original = path.read_bytes()
            self.assertEqual(json.loads(original)["deleted_passages"], 1)
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(path.read_bytes(), original)
