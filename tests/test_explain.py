"""Step 16 tests for concise grounded deterministic explanations."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from decision import Decision  # noqa: E402
from domain import AffordabilityStatus, CandidatePlan, Currency, PaymentMethod, ScheduledPayment  # noqa: E402
from explain import explain_decision  # noqa: E402
from ledger import EffectiveCashRecord, EffectiveFinancialState  # noqa: E402


STATE = EffectiveFinancialState("user_01", date(2026, 1, 10), Decimal("500"), Currency.EUR, (), (EffectiveCashRecord("pending", date(2026, 1, 11), Decimal("-20"), "event", "pending"),), ())


class ExplainTests(unittest.TestCase):
    def test_plan_template_includes_schedule_floor_and_changes_without_speculation(self) -> None:
        plan = CandidatePlan(PaymentMethod.PARTIAL_PAYMENT, (ScheduledPayment(date(2026, 1, 10), Decimal("50")), ScheduledPayment(date(2026, 1, 20), Decimal("50"))), Decimal("100"), None, ("reduce_to:event_01:20",))
        decision = Decision(AffordabilityStatus.AFFORDABLE_WITH_PLAN, PaymentMethod.PARTIAL_PAYMENT, plan, Decimal("50"), date(2026, 1, 20), "test")
        explanation = explain_decision(decision=decision, state=STATE, minimum_balance_to_keep=Decimal("100"))
        self.assertIn("EUR 50", explanation)
        self.assertIn("2026-01-20", explanation)
        self.assertIn("reduce_to:event_01:20", explanation)
        self.assertNotIn("pending credit", explanation.casefold())
        self.assertLess(len(explanation), 400)

    def test_fallback_template_is_concise_and_matches_no_recommendation(self) -> None:
        decision = Decision(AffordabilityStatus.NOT_AFFORDABLE, PaymentMethod.NOT_RECOMMENDED, None, Decimal("0"), None, "no plan")
        explanation = explain_decision(decision=decision, state=STATE, minimum_balance_to_keep=Decimal("100"))
        self.assertIn("Not recommended", explanation)
        self.assertIn("EUR 0", explanation)
        self.assertIn("within 90 days", explanation)
