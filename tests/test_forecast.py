"""Step 11 tests for pure conservative daily simulation."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from domain import Currency, ScheduledPayment  # noqa: E402
from forecast import simulate_daily_balances  # noqa: E402
from ledger import EffectiveCashRecord, EffectiveFinancialState  # noqa: E402


START = date(2026, 1, 1)


def state(opening: str, flows=(), reserved=()):  # type: ignore[no-untyped-def]
    return EffectiveFinancialState("user_01", START, Decimal(opening), Currency.EUR, tuple(flows), tuple(reserved), ())


def cash(identifier: str, when: date, amount: str) -> EffectiveCashRecord:
    return EffectiveCashRecord(identifier, when, Decimal(amount), "event", "test")


class ForecastTests(unittest.TestCase):
    def test_same_day_debit_precedes_credit_and_identifies_exact_breach(self) -> None:
        result = simulate_daily_balances(
            state=state("100", (cash("salary", START, "20"), cash("debit", START, "-60"))),
            minimum_balance_to_keep=Decimal("50"),
        )
        self.assertFalse(result.feasible)
        self.assertEqual((result.first_breach_date, result.first_breach_source_id), (START, "debit"))
        self.assertEqual(result.days[0].lowest_balance, Decimal("40"))
        self.assertEqual(result.days[0].closing_balance, Decimal("60"))

    def test_minimum_equality_is_feasible_and_ninety_day_is_inclusive(self) -> None:
        day_ninety = date(2026, 4, 1)
        result = simulate_daily_balances(
            state=state("100", (cash("boundary", day_ninety, "-50"),)),
            minimum_balance_to_keep=Decimal("50"),
        )
        self.assertTrue(result.feasible)
        self.assertEqual(len(result.days), 91)
        self.assertEqual(result.days[-1].cash_date, day_ninety)

    def test_plan_payment_beyond_horizon_is_checked_and_simulation_is_pure(self) -> None:
        input_state = state("150")
        payment = ScheduledPayment(date(2026, 4, 11), Decimal("60"))
        first = simulate_daily_balances(state=input_state, minimum_balance_to_keep=Decimal("100"), candidate_payments=(payment,))
        second = simulate_daily_balances(state=input_state, minimum_balance_to_keep=Decimal("100"), candidate_payments=(payment,))
        self.assertEqual(first, second)
        self.assertEqual(first.days[-1].cash_date, payment.payment_date)
        self.assertEqual(first.first_breach_source_id, "candidate_payment_1")
