"""Steps 9 and 10 tests for conservative state and recurrence rules."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from currency import CurrencyConverter  # noqa: E402
from domain import Currency  # noqa: E402
from evidence import MessageFact, MessageFactType  # noqa: E402
from ingest import load_dataset  # noqa: E402
from ledger import normalize_ledger_input, reconstruct_effective_financial_state  # noqa: E402
from recurrence import build_forecast_rules  # noqa: E402
from tests.test_ingest import fixture_rows, write_fixture  # noqa: E402
from tests.test_relationships import add_second_evaluation_option  # noqa: E402


AS_OF = date(2026, 1, 10)


def event(identifier: str, *, description: str, category: str, direction: str, amount: str, when: str, status: str = "settled", event_type: str = "expense") -> dict[str, str]:
    return {
        "event_id": identifier, "user_id": "user_01", "event_type": event_type,
        "description": description, "category": category, "direction": direction,
        "amount": amount, "currency": "EUR", "event_date": when, "settlement_date": when,
        "status": status, "linked_event_id": "", "flexibility": "fixed", "minimum_allowed_amount": "",
    }


def fact(identifier: str, kind: MessageFactType, *, amount: str | None = None, when: date | None = None, event_id: str | None = None, percentage: str | None = None) -> MessageFact:
    return MessageFact(identifier, "user_01", None, event_id, datetime(2026, 1, 1, tzinfo=timezone.utc), "bank", kind, Decimal(amount) if amount else None, Currency.EUR if amount else None, when, Decimal(percentage) if percentage else None, "source evidence")


class StateFixture(unittest.TestCase):
    def state(self, extra: list[dict[str, str]], facts=()):  # type: ignore[no-untyped-def]
        with tempfile.TemporaryDirectory() as temporary_directory:
            rows = fixture_rows()
            add_second_evaluation_option(rows)
            rows["financial_events.csv"].extend(extra)
            write_fixture(Path(temporary_directory), rows)
            dataset = load_dataset(Path(temporary_directory))
        normalized = normalize_ledger_input(dataset)
        return reconstruct_effective_financial_state(normalized=normalized, converter=CurrencyConverter(dataset.exchange_rates), user_id="user_01", request_date=AS_OF, message_facts=facts), normalized, dataset

    def test_reserves_pending_debit_but_never_counts_pending_credit(self) -> None:
        state, _, _ = self.state([
            event("pending_debit", description="Fuel", category="transport", direction="debit", amount="100", when="2026-01-12", status="pending"),
            event("pending_credit", description="Refund", category="refund", direction="credit", amount="200", when="2026-01-12", status="pending", event_type="refund"),
        ])
        self.assertEqual([(item.source_id, item.amount) for item in state.reserved_obligations], [("pending_debit", Decimal("-100"))])
        self.assertNotIn("pending_credit", [item.source_id for item in state.cash_flows])
        self.assertEqual(next(entry.disposition for entry in state.audit if entry.source_id == "pending_credit"), "excluded")

    def test_failed_retry_refund_and_unrealized_lifecycle_are_conservative(self) -> None:
        state, _, _ = self.state([
            event("failed", description="Insurance", category="insurance", direction="debit", amount="90", when="2026-01-11", status="failed"),
            event("refund", description="Settled refund", category="refund", direction="credit", amount="30", when="2026-01-12", event_type="refund"),
            event("valuation", description="Portfolio value", category="investment", direction="non_cash", amount="999", when="2026-01-12", status="unrealized", event_type="investment_valuation"),
            event("cancelled", description="Cancelled card authorization", category="shopping", direction="debit", amount="80", when="2026-01-11", status="cancelled"),
        ], facts=(fact("retry_message", MessageFactType.FAILED_DEBIT_RETRY, event_id="failed"),))
        self.assertEqual([(item.source_id, item.amount) for item in state.reserved_obligations], [("failed", Decimal("-90"))])
        self.assertEqual([(item.source_id, item.amount) for item in state.cash_flows], [("refund", Decimal("30"))])
        self.assertEqual(next(entry.disposition for entry in state.audit if entry.source_id == "valuation"), "excluded")

    def test_message_confirmed_income_is_dated_and_audited(self) -> None:
        state, _, _ = self.state([], facts=(fact("invoice_message", MessageFactType.CONFIRMED_INCOME, amount="250", when=date(2026, 1, 15)),))
        self.assertEqual(state.cash_flows[0].cash_date, date(2026, 1, 15))
        self.assertEqual(state.cash_flows[0].amount, Decimal("250"))
        self.assertEqual(state.dated_net_cash_flow[date(2026, 1, 15)], Decimal("250"))

    def test_settlement_evidence_and_internal_transfer_pair_override_raw_lifecycle(self) -> None:
        state, _, _ = self.state([
            event("settled_by_message", description="Utility", category="utilities", direction="debit", amount="60", when="2026-01-12", status="cancelled"),
            event("transfer_out", description="Transfer to savings", category="transfer", direction="debit", amount="200", when="2026-01-13"),
            event("transfer_in", description="Transfer from checking", category="transfer", direction="credit", amount="200", when="2026-01-13", event_type="income"),
        ], facts=(
            fact("settlement_message", MessageFactType.EVENT_SETTLED, event_id="settled_by_message"),
            fact("transfer_message", MessageFactType.INTERNAL_TRANSFER),
        ))
        self.assertEqual([(item.source_id, item.amount) for item in state.cash_flows], [("settled_by_message", Decimal("-60"))])
        self.assertEqual(
            {entry.source_id for entry in state.audit if entry.detail == "evidence-confirmed internal transfer pair"},
            {"transfer_out", "transfer_in"},
        )


class RecurrenceTests(StateFixture):
    def rules(self, extra: list[dict[str, str]], facts=()):  # type: ignore[no-untyped-def]
        _, normalized, dataset = self.state(extra, facts)
        return build_forecast_rules(normalized=normalized, converter=CurrencyConverter(dataset.exchange_rates), user_id="user_01", as_of_date=date(2026, 4, 20), message_facts=facts)

    def test_monthly_and_weekly_rules_are_supported_but_one_off_is_not(self) -> None:
        rules = self.rules([
            event("rent1", description="Rent", category="rent", direction="debit", amount="400", when="2026-02-01"),
            event("rent2", description="Rent", category="rent", direction="debit", amount="410", when="2026-03-01"),
            event("rent3", description="Rent", category="rent", direction="debit", amount="405", when="2026-04-01"),
            event("bus1", description="Bus pass", category="transport", direction="debit", amount="20", when="2026-04-01"),
            event("bus2", description="Bus pass", category="transport", direction="debit", amount="20", when="2026-04-08"),
            event("bus3", description="Bus pass", category="transport", direction="debit", amount="20", when="2026-04-15"),
            event("once", description="Camera", category="shopping", direction="debit", amount="500", when="2026-04-16"),
        ])
        rent = next(rule for rule in rules if rule.category == "rent")
        self.assertEqual((rent.cadence_days, rent.amount, rent.next_occurrence), (30, Decimal("410"), date(2026, 5, 1)))
        self.assertTrue(any(rule.category == "transport" and rule.cadence_days == 7 for rule in rules))
        self.assertFalse(any(rule.detail == "camera" for rule in rules))

    def test_message_amendments_and_confirmed_one_off_income(self) -> None:
        extra = [
            event("rent1", description="Rent", category="rent", direction="debit", amount="400", when="2026-02-01"), event("rent2", description="Rent", category="rent", direction="debit", amount="400", when="2026-03-01"), event("rent3", description="Rent", category="rent", direction="debit", amount="400", when="2026-04-01"),
            event("pay1", description="Salary", category="salary", direction="credit", amount="1000", when="2026-02-15", event_type="income"), event("pay2", description="Salary", category="salary", direction="credit", amount="1000", when="2026-03-15", event_type="income"), event("pay3", description="Salary", category="salary", direction="credit", amount="1000", when="2026-04-15", event_type="income"),
        ]
        facts = (fact("rent_msg", MessageFactType.RENT_INCREASE, percentage="0.10", when=date(2026, 4, 1)), fact("salary_msg", MessageFactType.SALARY_AMOUNT, amount="800", when=date(2026, 5, 15)), fact("invoice_msg", MessageFactType.CONFIRMED_INCOME, amount="125", when=date(2026, 5, 1)))
        rules = self.rules(extra, facts)
        self.assertEqual(next(rule.amount for rule in rules if rule.category == "rent"), Decimal("440.00"))
        self.assertEqual(next(rule.amount for rule in rules if rule.category == "salary"), Decimal("800"))
        self.assertTrue(any(rule.category == "confirmed_income" and rule.cadence_days is None for rule in rules))
        stopped = self.rules(extra, facts + (fact("end_msg", MessageFactType.INCOME_ENDED),))
        self.assertFalse(any(rule.category == "salary" for rule in stopped))
