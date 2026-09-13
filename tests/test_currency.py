"""Step 6 tests for exact, date-specific, supplied-rate conversion."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from currency import (  # noqa: E402
    ConversionStatus,
    CurrencyConverter,
    MissingExchangeRateError,
)
from domain import Currency, ExchangeRate  # noqa: E402
from ingest import load_dataset  # noqa: E402


DATE = date(2026, 1, 15)


def rate(source: Currency, target: Currency, value: str, on: date = DATE) -> ExchangeRate:
    return ExchangeRate(on, source, target, Decimal(value))


class CurrencyConversionTests(unittest.TestCase):
    def test_direct_conversion_uses_exact_decimal_rate_and_audit_leg(self) -> None:
        converter = CurrencyConverter((rate(Currency.USD, Currency.EUR, "0.92"),))

        result = converter.convert(
            source_id="event_01",
            amount=Decimal("100.50"),
            from_currency=Currency.USD,
            to_currency=Currency.EUR,
            rate_date=DATE,
        )

        self.assertEqual(result.converted_amount, Decimal("92.4600"))
        self.assertEqual(result.effective_rate, Decimal("0.92"))
        self.assertEqual(result.status, ConversionStatus.CONVERTED)
        self.assertEqual(tuple(leg.to_currency for leg in result.path), (Currency.EUR,))

    def test_identity_conversion_needs_no_supplied_rate(self) -> None:
        converter = CurrencyConverter(())

        result = converter.convert(
            source_id="event_02",
            amount=Decimal("123.45"),
            from_currency=Currency.INR,
            to_currency=Currency.INR,
            rate_date=DATE,
        )

        self.assertEqual(result.status, ConversionStatus.IDENTITY)
        self.assertEqual(result.converted_amount, Decimal("123.45"))
        self.assertEqual(result.path, ())

    def test_two_leg_path_is_deterministic_and_uses_only_supplied_same_date_rates(self) -> None:
        converter = CurrencyConverter(
            (
                rate(Currency.USD, Currency.INR, "80"),
                rate(Currency.INR, Currency.ZAR, "0.20"),
                rate(Currency.USD, Currency.EUR, "0.90"),
                rate(Currency.EUR, Currency.ZAR, "20"),
            )
        )

        result = converter.convert(
            source_id="event_03",
            amount=Decimal("10"),
            from_currency=Currency.USD,
            to_currency=Currency.ZAR,
            rate_date=DATE,
        )

        self.assertEqual(tuple(leg.to_currency for leg in result.path), (Currency.EUR, Currency.ZAR))
        self.assertEqual(result.effective_rate, Decimal("18.00"))
        self.assertEqual(result.converted_amount, Decimal("180.00"))

    def test_missing_date_specific_rate_is_audited_and_strict_mode_raises(self) -> None:
        converter = CurrencyConverter((rate(Currency.USD, Currency.EUR, "0.92"),))
        missing_date = date(2026, 1, 16)

        audit = converter.convert_with_audit(
            source_id="event_04",
            amount=Decimal("10"),
            from_currency=Currency.USD,
            to_currency=Currency.EUR,
            rate_date=missing_date,
        )
        self.assertEqual(audit.status, ConversionStatus.MISSING_RATE)
        self.assertIsNone(audit.converted_amount)
        self.assertIn("2026-01-16", audit.detail)
        with self.assertRaises(MissingExchangeRateError) as raised:
            converter.convert(
                source_id="event_04",
                amount=Decimal("10"),
                from_currency=Currency.USD,
                to_currency=Currency.EUR,
                rate_date=missing_date,
            )
        self.assertEqual(raised.exception.audit, audit)

    def test_rejects_float_and_duplicate_supplied_rate(self) -> None:
        converter = CurrencyConverter((rate(Currency.USD, Currency.EUR, "0.92"),))
        with self.assertRaises(TypeError):
            converter.convert(
                source_id="event_05",
                amount=10.0,  # type: ignore[arg-type]
                from_currency=Currency.USD,
                to_currency=Currency.EUR,
                rate_date=DATE,
            )
        with self.assertRaisesRegex(ValueError, "duplicate supplied exchange rate"):
            CurrencyConverter(
                (
                    rate(Currency.USD, Currency.EUR, "0.92"),
                    rate(Currency.USD, Currency.EUR, "0.93"),
                )
            )


class RealDatasetCurrencyTests(unittest.TestCase):
    def test_real_dataset_uses_supplied_rate_on_the_exact_settlement_date(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        dataset = load_dataset(repository_root / "dataset")
        converter = CurrencyConverter(dataset.exchange_rates)

        result = converter.convert(
            source_id="real-rate",
            amount=Decimal("10"),
            from_currency=Currency.USD,
            to_currency=Currency.ZAR,
            rate_date=date(2023, 10, 15),
        )
        self.assertEqual(tuple(leg.to_currency for leg in result.path), (Currency.EUR, Currency.ZAR))
        self.assertEqual(result.converted_amount, Decimal("184.00"))
