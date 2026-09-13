"""Step 2 tests for immutable domain records and exact monetary primitives."""

from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from domain import (  # noqa: E402
    AffordabilityStatus,
    CandidatePlan,
    Currency,
    Direction,
    EventStatus,
    EventType,
    FinancialEvent,
    FinancialProfile,
    Flexibility,
    PaymentMethod,
    Request,
    RequestType,
    ScheduledPayment,
    format_money,
    parse_date,
    parse_money,
    parse_optional_date,
    quantize_money,
)


class MoneyTests(unittest.TestCase):
    def test_parser_preserves_decimal_precision_and_rejects_float(self) -> None:
        self.assertEqual(parse_money("60383889.2"), Decimal("60383889.2"))
        self.assertEqual(parse_money(42), Decimal("42"))
        with self.assertRaises(TypeError):
            parse_money(0.1)

    def test_decimal_arithmetic_is_exact_at_dataset_scales(self) -> None:
        idr_balance = parse_money("60383889.2") + parse_money("0.8")
        eur_payment = parse_money("1852.115") + parse_money("0.005")

        self.assertEqual(idr_balance, Decimal("60383890.0"))
        self.assertEqual(quantize_money(eur_payment), Decimal("1852.12"))
        self.assertEqual(format_money(quantize_money(eur_payment)), "1852.12")

    def test_money_formatting_never_accepts_a_float(self) -> None:
        with self.assertRaises(TypeError):
            format_money(1.5)  # type: ignore[arg-type]


class DateTests(unittest.TestCase):
    def test_exact_iso_dates_and_internal_none(self) -> None:
        self.assertEqual(parse_date("2026-09-13"), date(2026, 9, 13))
        self.assertIsNone(parse_optional_date(None))
        with self.assertRaises(ValueError):
            parse_date("2026-9-13")
        with self.assertRaises(ValueError):
            parse_date("2026-09-13T10:00:00")


class DomainModelTests(unittest.TestCase):
    def test_profile_is_immutable_and_uses_enum_values(self) -> None:
        profile = FinancialProfile(
            user_id="user_01",
            home_currency=Currency.ZAR,
            current_available_balance=Decimal("58481.1"),
            minimum_balance_to_keep=Decimal("18000"),
            financial_priorities=frozenset({"education"}),
            protected_categories=frozenset({"rent"}),
            reducible_categories=frozenset({"dining"}),
            stoppable_categories=frozenset({"delivery_membership"}),
            payment_methods=frozenset({PaymentMethod.FULL_PAYMENT}),
            max_installment_months=None,
        )

        with self.assertRaises(FrozenInstanceError):
            profile.user_id = "other"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            FinancialProfile(
                user_id="user_01",
                home_currency="ZAR",  # type: ignore[arg-type]
                current_available_balance=Decimal("1"),
                minimum_balance_to_keep=Decimal("0"),
                financial_priorities=frozenset(),
                protected_categories=frozenset(),
                reducible_categories=frozenset(),
                stoppable_categories=frozenset(),
                payment_methods=frozenset(),
                max_installment_months=None,
            )

    def test_records_reject_raw_strings_and_float_amounts(self) -> None:
        with self.assertRaises(TypeError):
            Request(
                request_id="request_01",
                user_id="user_01",
                request_date=date(2024, 3, 3),
                request_type="purchase",  # type: ignore[arg-type]
                requested_amount=Decimal("25256"),
                desired_completion_date=date(2024, 3, 20),
                allows_partial_payment=True,
                request_text="Can I pay?",
            )
        with self.assertRaises(TypeError):
            FinancialEvent(
                event_id="event_01",
                user_id="user_01",
                event_type=EventType.EXPENSE,
                description="Rent",
                category="rent",
                direction=Direction.DEBIT,
                amount=5148.0,  # type: ignore[arg-type]
                currency=Currency.ZAR,
                event_date=date(2024, 3, 1),
                settlement_date=date(2024, 3, 1),
                status=EventStatus.SETTLED,
                linked_event_id=None,
                flexibility=Flexibility.FIXED,
                minimum_allowed_amount=None,
            )

    def test_candidate_plan_requires_typed_chronological_payments(self) -> None:
        first = ScheduledPayment(date(2025, 8, 8), Decimal("100"))
        second = ScheduledPayment(date(2025, 9, 7), Decimal("100"))
        plan = CandidatePlan(
            method=PaymentMethod.INSTALLMENTS,
            payments=(first, second),
            total_payable_amount=Decimal("200"),
            payment_option_id="payment_option_01",
        )
        self.assertEqual(plan.payments[0].payment_date, date(2025, 8, 8))
        with self.assertRaises(ValueError):
            CandidatePlan(
                method=PaymentMethod.INSTALLMENTS,
                payments=(second, first),
                total_payable_amount=Decimal("200"),
                payment_option_id="payment_option_01",
            )

    def test_allowed_output_values_are_centrally_enumerated(self) -> None:
        self.assertEqual(
            {status.value for status in AffordabilityStatus},
            {
                "affordable_now",
                "affordable_with_plan",
                "affordable_later",
                "not_affordable",
            },
        )
        self.assertEqual(
            {method.value for method in PaymentMethod},
            {"full_payment", "partial_payment", "installments", "wait", "not_recommended"},
        )
