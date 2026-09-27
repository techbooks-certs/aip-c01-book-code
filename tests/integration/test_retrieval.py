from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from claimsassist.baseline import BoundaryError
from claimsassist.rag_demo import build_index, compare
from claimsassist.retrieval import retrieve, validate_extracts


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.index = build_index()

    def run_query(self, **changes):
        args = dict(
            index=self.index,
            tenant="tenant-amber",
            loss_date="2026-09-20",
            query="water damage",
        )
        args.update(changes)
        return retrieve(**args)

    def test_filters_apply_to_all_modes(self):
        for mode in ("lexical", "vector", "hybrid"):
            result = self.run_query(mode=mode)
            text = json.dumps(result)
            self.assertNotIn("Private Birch", text)
            self.assertNotIn("Revoked private", text)
            self.assertTrue(result["evidence"])

    def test_missing_date_clarifies_before_index_access(self):
        class UnreadableIndex:
            def eligible_passages(self, *args):
                raise AssertionError("Index accessed without date")

        result = self.run_query(index=UnreadableIndex(), loss_date=None)
        self.assertEqual(result["status"], "clarification_required")
        self.assertEqual(result["evidence"], [])

    def test_unknown_query_abstains(self):
        self.assertEqual(
            self.run_query(query="airport lounge reimbursement")["status"], "abstain"
        )

    def test_exact_identifier_needs_lexical_path(self):
        self.assertEqual(
            self.run_query(query="pol-4821", mode="vector")["status"], "abstain"
        )
        for mode in ("lexical", "hybrid"):
            self.assertEqual(
                self.run_query(query="pol-4821", mode=mode)["evidence"][0]["source_id"],
                "pol-4821",
            )

    def test_validity_before_corpus_abstains(self):
        self.assertEqual(self.run_query(loss_date="2025-12-31")["status"], "abstain")

    def test_budget_never_slices_a_passage(self):
        result = self.run_query(max_chars=2)
        self.assertEqual(result["status"], "abstain")
        self.assertEqual(result["reason"], "context_budget_insufficient")
        self.assertEqual(result["context_chars"], 2)
        self.assertTrue(result["skipped_for_budget"])

    def test_reported_context_budget_matches_serialized_evidence(self):
        result = self.run_query(max_chars=600)
        actual = len(
            json.dumps(result["evidence"], ensure_ascii=False, separators=(",", ":"))
        )
        self.assertEqual(result["context_chars"], actual)
        self.assertLessEqual(actual, 600)

    def test_rerank_is_labeled_and_deterministic(self):
        a = self.run_query(rerank=True)
        b = self.run_query(rerank=True)
        self.assertEqual(a, b)
        self.assertEqual(a["reranker"], "lexical_overlap_heuristic")

    def test_invalid_parameters(self):
        for changes in [
            dict(mode="unknown"),
            dict(candidate_limit=True),
            dict(candidate_limit=21),
            dict(max_chars=1),
            dict(max_chars=10001),
            dict(query=""),
            dict(rerank=1),
            dict(tenant="tenant-other!"),
            dict(loss_date="yesterday"),
        ]:
            with self.subTest(changes=changes), self.assertRaises(BoundaryError):
                self.run_query(**changes)

    def test_valid_extracts_keep_citations_and_whole_quotes(self):
        evidence = self.run_query()["evidence"]
        response = {
            "extracts": [
                dict(citation_id=e["citation_id"], quote=e["quote"]) for e in evidence
            ]
        }
        self.assertEqual(validate_extracts(response, evidence), response)

    def test_unknown_citation_rejected(self):
        with self.assertRaises(BoundaryError):
            validate_extracts(
                {"extracts": [dict(citation_id="invented", quote="water")]},
                self.run_query()["evidence"],
            )

    def test_valid_citation_does_not_allow_altered_quote(self):
        evidence = self.run_query()["evidence"]
        with self.assertRaises(BoundaryError):
            validate_extracts(
                {
                    "extracts": [
                        dict(
                            citation_id=evidence[0]["citation_id"],
                            quote="All losses are covered.",
                        )
                    ]
                },
                evidence,
            )

    def test_arbitrary_answer_field_rejected(self):
        with self.assertRaises(BoundaryError):
            validate_extracts(
                {"extracts": [], "answer": "Approved"}, self.run_query()["evidence"]
            )

    def test_duplicate_extract_rejected(self):
        evidence = self.run_query()["evidence"]
        item = dict(citation_id=evidence[0]["citation_id"], quote=evidence[0]["quote"])
        with self.assertRaises(BoundaryError):
            validate_extracts({"extracts": [item, item]}, evidence)

    def test_chunking_failure_is_distinct_from_citation_identity(self):
        result = compare()
        bad = result["observations"][0]["cases"][0]
        good = result["observations"][3]["cases"][0]
        self.assertTrue(bad["expected_citation_found"])
        self.assertFalse(bad["required_text_preserved"])
        self.assertTrue(good["required_text_preserved"])
        self.assertEqual(sum(len(x["cases"]) for x in result["observations"]), 24)

    def test_cli_saves_and_preserves_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            cmd = [sys.executable, "-m", "claimsassist.rag_demo", "--output", str(path)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            original = path.read_bytes()
            self.assertEqual(
                json.loads(original)["evidence_class"], "offline_fixture_retrieval"
            )
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 2)
            self.assertEqual(path.read_bytes(), original)
