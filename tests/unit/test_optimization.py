import unittest
from decimal import Decimal

from claimsassist.baseline import BoundaryError
from claimsassist.optimization import *


class OptimizationTests(unittest.TestCase):
    def sample(self, **changes):
        values = dict(
            input_tokens=1000,
            output_tokens=200,
            retrieval_cost=Decimal(".001"),
            tool_cost=Decimal(".002"),
            human_minutes=Decimal("0"),
            success=True,
            quality=Decimal(".9"),
            safety_pass=True,
            ttft_ms=200,
            total_ms=800,
        )
        values.update(changes)
        return WorkflowSample(**values)

    def rates(self):
        return dict(
            input_per_million=Decimal("1"),
            output_per_million=Decimal("2"),
            human_per_minute=Decimal("1"),
        )

    def test_cost_includes_non_token_components(self):
        self.assertEqual(sample_cost(self.sample(), **self.rates()), Decimal(".0044"))

    def test_human_review_can_dominate_cost(self):
        self.assertGreater(
            sample_cost(self.sample(human_minutes=Decimal("2")), **self.rates()),
            Decimal("2"),
        )

    def test_cost_per_accepted_includes_failures(self):
        r = summarize([self.sample(), self.sample(success=False)], self.rates(), ".8")
        self.assertEqual(r["accepted"], 1)
        self.assertEqual(r["cost_per_accepted"], Decimal(".0088"))

    def test_quality_floor_rejects_cheap_failure(self):
        self.assertEqual(
            summarize([self.sample(quality=Decimal(".6"))], self.rates(), ".8")[
                "accepted"
            ],
            0,
        )

    def test_safety_is_release_gate(self):
        self.assertEqual(
            summarize([self.sample(safety_pass=False)], self.rates(), ".8")["accepted"],
            0,
        )

    def test_p95_uses_tail_sample(self):
        self.assertEqual(
            summarize(
                [self.sample(total_ms=x, ttft_ms=1) for x in range(1, 21)],
                self.rates(),
                ".8",
            )["p95_total_ms"],
            19,
        )

    def test_candidate_requires_all_constraints(self):
        good = {
            "cost_per_accepted": Decimal("1"),
            "p95_total_ms": 900,
            "acceptance_rate": Decimal(".9"),
            "mean_quality": Decimal(".9"),
            "safety_failures": 0,
        }
        bad = dict(good, p95_total_ms=2000)
        self.assertEqual(
            choose_candidate(
                "base",
                {"bad": bad, "good": good},
                max_cost_per_accepted=2,
                max_p95_ms=1000,
                min_acceptance_rate=0.8,
                min_quality=0.8,
            ),
            "good",
        )

    def test_no_eligible_candidate_keeps_baseline(self):
        bad = {
            "cost_per_accepted": None,
            "p95_total_ms": 1,
            "acceptance_rate": Decimal("0"),
            "mean_quality": Decimal("0"),
        }
        self.assertEqual(
            choose_candidate(
                "base",
                {"bad": bad},
                max_cost_per_accepted=2,
                max_p95_ms=1000,
                min_acceptance_rate=0.8,
                min_quality=0.8,
            ),
            "base",
        )

    def test_cache_can_cost_more_at_low_reuse(self):
        self.assertLess(
            prompt_cache_net(
                1000, 1, 0, standard_rate=1, write_multiplier=1.25, read_multiplier=0.1
            )["savings"],
            0,
        )

    def test_cache_saves_with_high_reuse(self):
        self.assertGreater(
            prompt_cache_net(
                1000,
                10,
                0.9,
                standard_rate=1,
                write_multiplier=1.25,
                read_multiplier=0.1,
            )["savings"],
            0,
        )

    def test_cache_key_changes_with_tenant(self):
        self.assertNotEqual(
            safe_cache_key(
                "a",
                "s",
                "p",
                "m",
                "q",
                authorization_scope="scope-1",
                permission_epoch="epoch-1",
            ),
            safe_cache_key(
                "b",
                "s",
                "p",
                "m",
                "q",
                authorization_scope="scope-1",
                permission_epoch="epoch-1",
            ),
        )

    def test_capacity_uses_arrival_service_and_headroom(self):
        self.assertEqual(required_concurrency(4, 2, 1.25), 10)

    def test_latency_waterfall_finds_measured_bottleneck(self):
        self.assertEqual(
            latency_waterfall({"retrieval": 80, "model": 500, "tool": 120})[
                "largest_stage"
            ],
            "model",
        )

    def test_invalid_latency_is_rejected(self):
        with self.assertRaises(BoundaryError):
            latency_waterfall({"model": -1})
