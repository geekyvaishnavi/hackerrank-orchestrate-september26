"""Step 12 tests for safe-payment capacity and earliest full-payment date."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from decision import calculate_payment_capacity  # noqa: E402
from domain import Currency  # noqa: E402
from ledger import EffectiveCashRecord, EffectiveFinancialState  # noqa: E402


START = date(2026, 1, 1)


def state(opening: str, flows=()):  # type: ignore[no-untyped-def]
    return EffectiveFinancialState("user_01", START, Decimal(opening), Currency.EUR, tuple(flows), (), ())


def cash(identifier: str, offset: int, amount: str) -> EffectiveCashRecord:
    return EffectiveCashRecord(identifier, date.fromordinal(START.toordinal() + offset), Decimal(amount), "event", "test")


class CapacityTests(unittest.TestCase):
    def test_zero_partial_and_full_caps_are_bounded(self) -> None:
        cases = (("100", "100", "70", "0"), ("150", "100", "70", "50"), ("300", "100", "70", "70"))
        for opening, minimum, request, expected in cases:
            capacity = calculate_payment_capacity(state=state(opening), minimum_balance_to_keep=Decimal(minimum), requested_amount=Decimal(request))
            self.assertEqual(capacity.amount_safe_to_pay, Decimal(expected))
            self.assertGreaterEqual(capacity.amount_safe_to_pay, Decimal("0"))
            self.assertLessEqual(capacity.amount_safe_to_pay, Decimal(request))

    def test_first_full_payment_is_after_confirmed_income_under_debit_first_ordering(self) -> None:
        capacity = calculate_payment_capacity(
            state=state("150", (cash("salary", 5, "200"),)),
            minimum_balance_to_keep=Decimal("100"), requested_amount=Decimal("200"),
        )
        self.assertEqual(capacity.amount_safe_to_pay, Decimal("50"))
        self.assertEqual(capacity.earliest_date_for_full_payment, date(2026, 1, 7))

    def test_later_obligation_limits_cap_and_can_prevent_full_payment_in_horizon(self) -> None:
        capacity = calculate_payment_capacity(
            state=state("400", (cash("essential", 10, "-200"),)),
            minimum_balance_to_keep=Decimal("100"), requested_amount=Decimal("200"),
        )
        self.assertEqual(capacity.amount_safe_to_pay, Decimal("100"))
        self.assertIsNone(capacity.earliest_date_for_full_payment)
