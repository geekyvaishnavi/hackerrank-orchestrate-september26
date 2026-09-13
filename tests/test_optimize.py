"""Step 14 tests for permitted, minimal prospective spending changes."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from domain import CandidatePlan, Currency, PaymentMethod, ScheduledPayment  # noqa: E402
from ingest import load_dataset  # noqa: E402
from ledger import EffectiveFinancialState, normalize_ledger_input  # noqa: E402
from optimize import optimize_spending_changes  # noqa: E402
from recurrence import ForecastRule  # noqa: E402
from tests.test_ingest import fixture_rows, write_fixture  # noqa: E402
from tests.test_relationships import add_second_evaluation_option  # noqa: E402


class OptimizeTests(unittest.TestCase):
    def inputs(self, *, category: str, flexibility: str, floor: str = ""):
        with tempfile.TemporaryDirectory() as temporary_directory:
            rows = fixture_rows()
            add_second_evaluation_option(rows)
            profile = rows["financial_profiles.csv"][0]
            profile["expense_categories_to_protect"] = "rent|groceries"
            profile["expense_categories_user_is_willing_to_reduce"] = "dining"
            profile["expense_categories_user_is_willing_to_stop"] = "streaming"
            row = rows["financial_events.csv"][0]
            row.update(category=category, flexibility=flexibility, minimum_allowed_amount=floor, settlement_date="2026-01-11", event_date="2026-01-11")
            write_fixture(Path(temporary_directory), rows)
            dataset = load_dataset(Path(temporary_directory))
        normalized = normalize_ledger_input(dataset)
        state = EffectiveFinancialState("user_01", date(2026, 1, 10), Decimal("2000"), Currency.EUR, (), (), ())
        rule = ForecastRule("user_01", category, __import__("domain").Direction.DEBIT, 30, date(2026, 1, 11), Decimal("400"), ("event_01",), (), Decimal("1"), "test", "test")
        candidate = CandidatePlan(PaymentMethod.FULL_PAYMENT, (ScheduledPayment(date(2026, 1, 10), Decimal("1000")),), Decimal("1000"), "payment_option_01")
        return normalized, state, rule, candidate

    def test_stop_and_reduction_are_permitted_at_the_legal_floor(self) -> None:
        normalized, state, rule, candidate = self.inputs(category="streaming", flexibility="stoppable")
        stopped = optimize_spending_changes(base_candidate=candidate, state=state, normalized=normalized, rules=(rule,))
        self.assertEqual(stopped.actions, ("stop:event_01",))
        normalized, state, rule, candidate = self.inputs(category="dining", flexibility="reducible", floor="100")
        reduced = optimize_spending_changes(base_candidate=candidate, state=state, normalized=normalized, rules=(rule,))
        self.assertEqual(reduced.actions, ("reduce_to:event_01:100",))

    def test_protected_fixed_and_already_safe_candidates_are_not_changed(self) -> None:
        normalized, state, rule, candidate = self.inputs(category="rent", flexibility="stoppable")
        protected = optimize_spending_changes(base_candidate=candidate, state=state, normalized=normalized, rules=(rule,))
        self.assertIsNone(protected.candidate)
        normalized, state, rule, candidate = self.inputs(category="streaming", flexibility="fixed")
        fixed = optimize_spending_changes(base_candidate=candidate, state=state, normalized=normalized, rules=(rule,))
        self.assertIsNone(fixed.candidate)
        safe = CandidatePlan(PaymentMethod.FULL_PAYMENT, (ScheduledPayment(date(2026, 1, 10), Decimal("100")),), Decimal("100"), "payment_option_01")
        unchanged = optimize_spending_changes(base_candidate=safe, state=state, normalized=normalized, rules=(rule,))
        self.assertEqual(unchanged.actions, ())
