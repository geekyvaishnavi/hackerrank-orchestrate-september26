"""Step 13 tests for source-exact payment-plan candidate enumeration."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from decision import PaymentCapacity  # noqa: E402
from domain import Currency  # noqa: E402
from ledger import EffectiveFinancialState, normalize_ledger_input  # noqa: E402
from plans import enumerate_plan_candidates  # noqa: E402
from ingest import load_dataset  # noqa: E402
from tests.test_ingest import fixture_rows, write_fixture  # noqa: E402
from tests.test_relationships import add_second_evaluation_option  # noqa: E402


class PlanTests(unittest.TestCase):
    def inputs(self, *, methods="full_payment|installments", maximum="6"):
        with tempfile.TemporaryDirectory() as temporary_directory:
            rows = fixture_rows()
            add_second_evaluation_option(rows)
            rows["financial_profiles.csv"][0]["payment_methods_user_will_consider"] = methods
            rows["financial_profiles.csv"][0]["max_installment_months"] = maximum
            write_fixture(Path(temporary_directory), rows)
            dataset = load_dataset(Path(temporary_directory))
        state = EffectiveFinancialState("user_01", date(2026, 1, 10), Decimal("1000"), Currency.EUR, (), (), ())
        return dataset, normalize_ledger_input(dataset), state

    def test_full_and_installment_options_preserve_provider_schedule(self) -> None:
        dataset, normalized, state = self.inputs()
        results = enumerate_plan_candidates(request=dataset.requests[0], normalized=normalized, state=state, capacity=PaymentCapacity(Decimal("300.25"), date(2026, 1, 10)))
        full = next(item for item in results if item.label == "payment_option_01")
        installment = next(item for item in results if item.label == "payment_option_02")
        self.assertTrue(full.eligible)
        self.assertEqual(full.candidate.payments[0].amount, Decimal("300.25"))
        self.assertTrue(installment.eligible)
        self.assertEqual(len(installment.candidate.payments), 2)
        self.assertEqual(sum(payment.amount for payment in installment.candidate.payments), Decimal("310.00"))

    def test_disallowed_term_and_bad_schedule_have_explicit_reasons(self) -> None:
        dataset, normalized, state = self.inputs(methods="installments", maximum="")
        results = enumerate_plan_candidates(request=dataset.requests[0], normalized=normalized, state=state, capacity=PaymentCapacity(Decimal("0"), None))
        self.assertIn("payment method not accepted", next(item.reason for item in results if item.label == "payment_option_01"))
        self.assertIn("term exceeds", next(item.reason for item in results if item.label == "payment_option_02"))

    def test_partial_and_wait_follow_strict_deadline_rules(self) -> None:
        dataset, normalized, state = self.inputs(methods="full_payment|installments|partial_payment")
        request = dataset.requests[0]
        capacity = PaymentCapacity(Decimal("100"), date(2026, 1, 20))
        results = enumerate_plan_candidates(request=request, normalized=normalized, state=state, capacity=capacity)
        partial = next(item for item in results if item.label == "partial_payment")
        wait = next(item for item in results if item.label == "wait")
        self.assertTrue(partial.eligible)
        self.assertEqual(len(partial.candidate.payments), 2)
        self.assertEqual(sum(payment.amount for payment in partial.candidate.payments), request.requested_amount)
        self.assertTrue(wait.eligible)
        late = enumerate_plan_candidates(request=request, normalized=normalized, state=state, capacity=PaymentCapacity(Decimal("100"), date(2026, 3, 1)))
        self.assertFalse(next(item for item in late if item.label == "partial_payment").eligible)
