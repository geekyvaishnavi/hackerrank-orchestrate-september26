"""Step 15 tests for deterministic candidate selection and fallback."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from decision import PaymentCapacity, select_decision  # noqa: E402
from domain import CandidatePlan, PaymentMethod, Request, RequestType, ScheduledPayment  # noqa: E402
from plans import PlanAssessment  # noqa: E402


REQUEST = Request("request_01", "user_01", date(2026, 1, 10), RequestType.PURCHASE, Decimal("100"), date(2026, 2, 10), True, "test")


def candidate(method: PaymentMethod, amount: str, when: date, *, option="payment_option_02", changes=()):
    return CandidatePlan(method, (ScheduledPayment(when, Decimal(amount)),), Decimal(amount), option, changes)


class DecisionTests(unittest.TestCase):
    def test_prefers_no_changes_then_lower_cost_then_earlier_option(self) -> None:
        changed = candidate(PaymentMethod.FULL_PAYMENT, "90", REQUEST.request_date, changes=("stop:event_01",))
        later = candidate(PaymentMethod.FULL_PAYMENT, "100", date(2026, 1, 11), option="payment_option_01")
        immediate = candidate(PaymentMethod.FULL_PAYMENT, "100", REQUEST.request_date, option="payment_option_03")
        decision = select_decision(
            request=REQUEST, capacity=PaymentCapacity(Decimal("100"), REQUEST.request_date),
            assessments=(PlanAssessment("changed", changed, None, True, None), PlanAssessment("later", later, None, True, None), PlanAssessment("immediate", immediate, None, True, None)),
        )
        self.assertEqual(decision.candidate, immediate)
        self.assertEqual(decision.recommended_payment_method, PaymentMethod.FULL_PAYMENT)
        self.assertEqual(decision.affordability_status.value, "affordable_now")

    def test_wait_and_safe_plan_statuses_and_no_candidate_fallback(self) -> None:
        wait = candidate(PaymentMethod.WAIT, "100", date(2026, 1, 15), option=None)
        decision = select_decision(request=REQUEST, capacity=PaymentCapacity(Decimal("20"), date(2026, 1, 15)), assessments=(PlanAssessment("wait", wait, None, True, None),))
        self.assertEqual(decision.affordability_status.value, "affordable_later")
        installment = candidate(PaymentMethod.INSTALLMENTS, "100", REQUEST.request_date)
        planned = select_decision(request=REQUEST, capacity=PaymentCapacity(Decimal("20"), date(2026, 1, 15)), assessments=(PlanAssessment("installments", installment, None, True, None),))
        self.assertEqual(planned.affordability_status.value, "affordable_with_plan")
        fallback = select_decision(request=REQUEST, capacity=PaymentCapacity(Decimal("0"), None), assessments=(PlanAssessment("full", None, None, False, "payment method not accepted by profile"),))
        self.assertEqual(fallback.recommended_payment_method, PaymentMethod.NOT_RECOMMENDED)
        self.assertIn("payment method", fallback.reason)
