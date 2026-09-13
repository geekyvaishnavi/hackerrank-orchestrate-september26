"""Step 3 tests for CSV schemas, typed parsing, indexes, and input integrity."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from domain import Currency, EventStatus  # noqa: E402
from ingest import DataValidationError, REQUIRED_HEADERS, load_dataset  # noqa: E402


def write_csv(directory: Path, filename: str, rows: list[dict[str, str]]) -> None:
    with (directory / filename).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUIRED_HEADERS[filename])
        writer.writeheader()
        writer.writerows(rows)


def fixture_rows() -> dict[str, list[dict[str, str]]]:
    profile = {
        "user_id": "user_01",
        "home_currency": "EUR",
        "current_available_balance": "1000.50",
        "minimum_balance_to_keep": "200.25",
        "financial_priorities": "education|housing",
        "expense_categories_to_protect": "rent|groceries",
        "expense_categories_user_is_willing_to_reduce": "dining",
        "expense_categories_user_is_willing_to_stop": "streaming",
        "payment_methods_user_will_consider": "full_payment|installments",
        "max_installment_months": "6",
    }
    event = {
        "event_id": "event_01",
        "user_id": "user_01",
        "event_type": "expense",
        "description": "Rent",
        "category": "rent",
        "direction": "debit",
        "amount": "400.00",
        "currency": "EUR",
        "event_date": "2026-01-01",
        "settlement_date": "2026-01-01",
        "status": "settled",
        "linked_event_id": "",
        "flexibility": "fixed",
        "minimum_allowed_amount": "",
    }
    request = {
        "request_id": "request_01",
        "user_id": "user_01",
        "request_date": "2026-01-10",
        "request_type": "purchase",
        "requested_amount": "300.25",
        "desired_completion_date": "2026-02-10",
        "allows_partial_payment": "true",
        "request_text": "Can I buy this?",
    }
    sample_request = request | {"request_id": "sample_request_01"}
    output = {column: "" for column in REQUIRED_HEADERS["output.csv"]}
    output["request_id"] = "request_01"
    return {
        "financial_profiles.csv": [profile],
        "financial_events.csv": [event],
        "exchange_rates.csv": [
            {"rate_date": "2026-01-01", "from_currency": "EUR", "to_currency": "USD", "rate": "1.10"}
        ],
        "requests.csv": [request],
        "sample_requests.csv": [sample_request | {column: "" for column in REQUIRED_HEADERS["output.csv"][1:]}],
        "request_payment_options.csv": [
            {
                "payment_option_id": "payment_option_01",
                "request_id": "request_01",
                "payment_method": "full_payment",
                "payment_amount": "300.25",
                "number_of_payments": "1",
                "first_payment_date": "2026-01-10",
                "payment_frequency_days": "",
                "financing_fee": "0",
                "total_payable_amount": "300.25",
            }
        ],
        "messages.csv": [
            {
                "message_id": "message_01",
                "user_id": "user_01",
                "request_id": "request_01",
                "related_event_id": "event_01",
                "sent_at": "2026-01-02T09:30:00Z",
                "source_type": "bank",
                "message_text": "Payment settled.",
            }
        ],
        "images.csv": [
            {"image_id": "image_01", "user_id": "user_01", "request_id": "request_01", "related_event_id": "event_01"}
        ],
        "output.csv": [output],
    }


def write_fixture(directory: Path, rows: dict[str, list[dict[str, str]]] | None = None) -> None:
    for filename, file_rows in (rows or fixture_rows()).items():
        write_csv(directory, filename, file_rows)


class IngestFixtureTests(unittest.TestCase):
    def load_fixture(self, mutate=None):  # type: ignore[no-untyped-def]
        temporary_directory = tempfile.TemporaryDirectory()
        directory = Path(temporary_directory.name)
        rows = fixture_rows()
        if mutate:
            mutate(rows)
        write_fixture(directory, rows)
        self.addCleanup(temporary_directory.cleanup)
        return load_dataset(directory)

    def test_parses_typed_records_builds_indexes_and_reports_blanks(self) -> None:
        dataset = self.load_fixture()

        self.assertEqual(dataset.profiles_by_user["user_01"].home_currency, Currency.EUR)
        self.assertEqual(dataset.requests_by_id["request_01"].requested_amount, Decimal("300.25"))
        self.assertEqual(dataset.events_by_id["event_01"].status, EventStatus.SETTLED)
        self.assertEqual(dataset.payment_options_by_request["request_01"][0].payment_option_id, "payment_option_01")
        self.assertEqual(dataset.report.row_counts["requests.csv"], 1)
        self.assertEqual(dataset.report.optional_blank_counts["financial_events.linked_event_id"], 1)

    def test_rejects_header_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            write_fixture(directory)
            (directory / "requests.csv").write_text("wrong_header\nvalue\n", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "header mismatch"):
                load_dataset(directory)

    def test_rejects_malformed_boolean_and_decimal(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "expected true or false"):
            self.load_fixture(lambda rows: rows["requests.csv"][0].update(allows_partial_payment="TRUE"))
        with self.assertRaisesRegex(DataValidationError, "requested_amount"):
            self.load_fixture(lambda rows: rows["requests.csv"][0].update(requested_amount="not-money"))

    def test_rejects_duplicate_primary_id(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "duplicate request_id"):
            self.load_fixture(lambda rows: rows["requests.csv"].append(rows["requests.csv"][0].copy()))

    def test_rejects_unmatched_profile_and_event_references(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "unknown user_id"):
            self.load_fixture(lambda rows: rows["requests.csv"][0].update(user_id="unknown_user"))
        with self.assertRaisesRegex(DataValidationError, "unknown related_event_id"):
            self.load_fixture(lambda rows: rows["messages.csv"][0].update(related_event_id="unknown_event"))

    def test_rejects_output_template_id_mismatch(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "request_id set mismatch"):
            self.load_fixture(lambda rows: rows["output.csv"][0].update(request_id="wrong_request"))


class RealDatasetTests(unittest.TestCase):
    def test_real_participant_dataset_loads_cleanly(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        dataset = load_dataset(repository_root / "dataset")

        self.assertEqual(len(dataset.profiles), 275)
        self.assertEqual(len(dataset.events), 25342)
        self.assertEqual(len(dataset.requests), 250)
        self.assertEqual(len(dataset.output_template_request_ids), 250)
        self.assertEqual(len(dataset.payment_options), 790)
        self.assertEqual(dataset.report.optional_blank_counts["financial_events.amount"], 16)
