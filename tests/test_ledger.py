"""Step 5 tests for canonical event and payment-option normalization."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from domain import Currency, Direction, EventStatus, EventType, FinancialEvent, Flexibility  # noqa: E402
from ingest import load_dataset  # noqa: E402
from ledger import (  # noqa: E402
    normalize_category,
    normalize_event,
    normalize_ledger_input,
    normalize_payment_option,
)
from tests.test_ingest import fixture_rows, write_fixture  # noqa: E402
from tests.test_relationships import add_second_evaluation_option  # noqa: E402


class EventNormalizationTests(unittest.TestCase):
    def test_event_uses_settlement_date_and_debit_credit_signs(self) -> None:
        debit = FinancialEvent(
            event_id="event_debit",
            user_id="user_01",
            event_type=EventType.EXPENSE,
            description="Dining",
            category=" Dining-Out ",
            direction=Direction.DEBIT,
            amount=Decimal("123.45"),
            currency=Currency.EUR,
            event_date=date(2026, 1, 2),
            settlement_date=date(2026, 1, 4),
            status=EventStatus.SETTLED,
            linked_event_id=None,
            flexibility=Flexibility.REDUCIBLE,
            minimum_allowed_amount=Decimal("20"),
        )
        normalized_debit = normalize_event(debit)
        self.assertEqual(normalized_debit.cash_date, date(2026, 1, 4))
        self.assertEqual(normalized_debit.signed_source_amount, Decimal("-123.45"))
        self.assertEqual(normalized_debit.category, "dining_out")

        # Construct a distinct credit explicitly because frozen/slots records are immutable.
        credit = FinancialEvent(
            event_id="event_credit",
            user_id="user_01",
            event_type=EventType.INCOME,
            description="Salary",
            category="salary",
            direction=Direction.CREDIT,
            amount=Decimal("500"),
            currency=Currency.EUR,
            event_date=date(2026, 1, 5),
            settlement_date=None,
            status=EventStatus.SCHEDULED,
            linked_event_id=None,
            flexibility=Flexibility.FIXED,
            minimum_allowed_amount=None,
        )
        normalized_credit = normalize_event(credit)
        self.assertEqual(normalized_credit.cash_date, date(2026, 1, 5))
        self.assertEqual(normalized_credit.signed_source_amount, Decimal("500"))

    def test_non_cash_and_missing_amount_remain_unresolved_not_zero(self) -> None:
        event = FinancialEvent(
            event_id="event_non_cash",
            user_id="user_01",
            event_type=EventType.INVESTMENT_VALUATION,
            description="Portfolio value",
            category="investment",
            direction=Direction.NON_CASH,
            amount=Decimal("900"),
            currency=Currency.EUR,
            event_date=date(2026, 1, 5),
            settlement_date=None,
            status=EventStatus.UNREALIZED,
            linked_event_id=None,
            flexibility=Flexibility.FIXED,
            minimum_allowed_amount=None,
        )
        normalized = normalize_event(event)

        self.assertEqual(normalized.source_amount, Decimal("900"))
        self.assertIsNone(normalized.signed_source_amount)
        missing_amount = normalize_event(replace(event, event_id="event_missing", amount=None))
        self.assertIsNone(missing_amount.source_amount)
        self.assertIsNone(missing_amount.signed_source_amount)

    def test_category_normalization_is_stable(self) -> None:
        self.assertEqual(normalize_category("  Delivery Membership  "), "delivery_membership")
        self.assertEqual(normalize_category("delivery-membership"), "delivery_membership")


class PaymentOptionNormalizationTests(unittest.TestCase):
    def test_payment_schedule_preserves_provider_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            rows = fixture_rows()
            add_second_evaluation_option(rows)
            write_fixture(directory, rows)
            dataset = load_dataset(directory)
        full_payment = normalize_payment_option(dataset.payment_options_by_id["payment_option_01"])
        installments = normalize_payment_option(dataset.payment_options_by_id["payment_option_02"])

        self.assertTrue(full_payment.schedule.is_one_time)
        self.assertIsNone(full_payment.schedule.payment_frequency_days)
        self.assertEqual(installments.schedule.number_of_payments, 2)
        self.assertEqual(installments.schedule.payment_frequency_days, 30)
        self.assertEqual(installments.financing_fee, Decimal("9.75"))
        self.assertEqual(installments.total_payable_amount, Decimal("310.00"))


class LedgerInputNormalizationTests(unittest.TestCase):
    def test_normalized_input_indexes_and_preferences_without_currency_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            rows = fixture_rows()
            add_second_evaluation_option(rows)
            write_fixture(directory, rows)
            normalized = normalize_ledger_input(load_dataset(directory))

        event = normalized.events_by_id["event_01"]
        preferences = normalized.preferences_by_user["user_01"]
        self.assertEqual(event.source_amount, Decimal("400.00"))
        self.assertEqual(event.signed_source_amount, Decimal("-400.00"))
        self.assertEqual(event.currency.value, "EUR")
        self.assertEqual(preferences.protected_categories, frozenset({"rent", "groceries"}))
        self.assertEqual(preferences.reducible_categories, frozenset({"dining"}))
        self.assertEqual(len(normalized.payment_options_by_request["request_01"]), 2)


class RealDatasetNormalizationTests(unittest.TestCase):
    def test_real_dataset_normalizes_all_events_and_options(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        normalized = normalize_ledger_input(load_dataset(repository_root / "dataset"))

        self.assertEqual(len(normalized.events), 25342)
        self.assertEqual(len(normalized.payment_options), 790)
        self.assertEqual(len(normalized.preferences_by_user), 275)
        self.assertEqual(normalized.events_by_id["event_01"].signed_source_amount, Decimal("-5148"))
