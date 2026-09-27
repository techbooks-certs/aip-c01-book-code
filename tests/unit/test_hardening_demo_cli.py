"""Direct coverage for claimsassist.hardening_demo's own functions and CLI.

Nothing else in the suite exercises safety()/governance()/optimization()/
observability()/evaluation() or main() directly -- the module is designed to be
run by hand from the Chapter 9-13 lab READMEs (PYTHONPATH=src python -m
claimsassist.hardening_demo --chapter N --output ...), so it had no automated
regression coverage at all before CP-F1. Passing tests elsewhere in the suite
proved the functions this module *calls* behave correctly; nothing proved this
module's own composition and CLI wiring did.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from claimsassist.hardening_demo import (
    safety,
    governance,
    optimization,
    observability,
    evaluation,
    main,
)


class HardeningDemoFunctionTests(unittest.TestCase):
    """Each Chapter 9-13 fixture function must execute cleanly end to end."""

    def test_all_five_chapter_functions_execute_without_error(self):
        for name, fn in [
            ("safety", safety),
            ("governance", governance),
            ("optimization", optimization),
            ("observability", observability),
            ("evaluation", evaluation),
        ]:
            with self.subTest(chapter_function=name):
                result = fn()
                self.assertIsInstance(result, dict)
                self.assertIn("interpretation", result)

    def test_safety_confusion_matrix_sums_to_case_count(self):
        result = safety()
        self.assertEqual(len(result["cases"]), 6)
        self.assertEqual(sum(result["confusion_matrix"].values()), 6)
        # The module deliberately demonstrates one false positive and one
        # false negative from its literal-pattern guardrail fixture.
        self.assertEqual(result["confusion_matrix"]["false_positive"], 1)
        self.assertEqual(result["confusion_matrix"]["false_negative"], 1)

    def test_governance_cross_tenant_denied_and_private_field_removed(self):
        result = governance()
        self.assertTrue(result["cross_tenant_denied"])
        self.assertTrue(result["private_field_removed"])
        self.assertFalse(result["before_cleanup"]["complete"])
        self.assertTrue(result["after_cleanup"]["complete"])

    def test_evaluation_rejects_holdout_leak(self):
        result = evaluation()
        self.assertTrue(result["holdout_leak_rejected"])


class HardeningDemoCliTests(unittest.TestCase):
    """Regression: main() must fail cleanly (exit 2, no traceback) instead of
    letting exclusive-create raise an unhandled FileExistsError/FileNotFoundError,
    matching the guard pattern used by cli.py and deployment_cli.py.
    """

    def run_main(self, argv):
        with patch("sys.argv", ["hardening_demo.py"] + argv):
            main()

    def test_first_run_succeeds_and_second_run_at_same_path_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ch9.json"
            self.run_main(["--chapter", "9", "--output", str(out)])
            self.assertTrue(out.exists())
            with self.assertRaises(SystemExit) as ctx:
                self.run_main(["--chapter", "9", "--output", str(out)])
            self.assertEqual(ctx.exception.code, 2)

    def test_missing_parent_directory_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "missing_subdir" / "ch10.json"
            with self.assertRaises(SystemExit) as ctx:
                self.run_main(["--chapter", "10", "--output", str(out)])
            self.assertEqual(ctx.exception.code, 2)
            self.assertFalse(out.exists())

    def test_every_chapter_runs_through_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            for chapter in range(9, 14):
                out = Path(tmp) / f"ch{chapter}.json"
                with self.subTest(chapter=chapter):
                    self.run_main(
                        ["--chapter", str(chapter), "--output", str(out)]
                    )
                    self.assertTrue(out.exists())
