"""Step 8 tests for constrained message fact extraction."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from domain import Message  # noqa: E402
from evidence import MessageFactType, extract_message_facts, resolve_message_conflicts  # noqa: E402
from ingest import load_dataset  # noqa: E402


def message(text: str, *, message_id: str = "message_01", when: int = 1, event: str | None = None) -> Message:
    return Message(message_id, "user_01", None, event, datetime(2026, 1, when, tzinfo=timezone.utc), "employer", text)


class MessageEvidenceTests(unittest.TestCase):
    def test_extracts_salary_change_in_english_and_indonesian(self) -> None:
        facts = extract_message_facts((
            message("Your monthly salary has increased to USD 2988. The change applies from 2026-07-15."),
            message("Gaji bulanan Anda naik menjadi IDR 17290000. Perubahan ini berlaku mulai 2026-07-15.", message_id="message_02"),
        ))
        self.assertEqual([fact.fact_type for fact in facts], [MessageFactType.SALARY_AMOUNT, MessageFactType.SALARY_AMOUNT])
        self.assertEqual(facts[0].amount, Decimal("2988"))
        self.assertEqual(facts[1].amount, Decimal("17290000"))

    def test_extracts_confirmed_invoice_and_conservative_pending_credit(self) -> None:
        facts = extract_message_facts((
            message("The client approved an invoice payment of INR 116000. Settlement is expected on 2026-04-15."),
            message("Your refund has been initiated but has not reached your account yet.", message_id="message_02"),
        ))
        self.assertEqual(facts[0].fact_type, MessageFactType.CONFIRMED_INCOME)
        self.assertEqual(facts[0].amount, Decimal("116000"))
        self.assertEqual(facts[1].fact_type, MessageFactType.PENDING_CREDIT)
        self.assertIsNone(facts[1].amount)

    def test_extracts_retry_and_internal_transfer_but_ignores_embedded_payment_instruction(self) -> None:
        facts = extract_message_facts((
            message("The previous debit attempt failed. The bill is still outstanding and another debit will be attempted.", event="event_01"),
            message("The matching debit and credit came from a transfer between your two accounts.", message_id="message_02"),
            message("Congratulations! Pay the release charge today to receive the funds immediately.", message_id="message_03"),
        ))
        self.assertEqual([fact.fact_type for fact in facts], [MessageFactType.FAILED_DEBIT_RETRY, MessageFactType.INTERNAL_TRANSFER])

    def test_newer_fact_from_same_scope_wins_conflict_resolution(self) -> None:
        facts = extract_message_facts((
            message("Your monthly salary has increased to EUR 1000.", when=1),
            message("Your monthly salary has increased to EUR 1200.", message_id="message_02", when=2),
        ))
        resolved = resolve_message_conflicts(facts)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].amount, Decimal("1200"))

    def test_extracts_income_end_rent_amendment_and_linked_settlement(self) -> None:
        facts = extract_message_facts((
            message("Your current seasonal contract has ended. No renewal has been confirmed."),
            message(
                "The renewed lease increases monthly rent by 12%. The new amount applies from 2026-06-01.",
                message_id="message_02",
            ),
            message(
                "Your property maintenance payment was received on 2026-06-03.",
                message_id="message_03",
                event="event_01",
            ),
            message("A newsletter about local sports results.", message_id="message_04"),
        ))
        self.assertEqual(
            [fact.fact_type for fact in facts],
            [
                MessageFactType.INCOME_ENDED,
                MessageFactType.RENT_INCREASE,
                MessageFactType.EVENT_SETTLED,
            ],
        )
        self.assertEqual(facts[1].percentage, Decimal("0.12"))
        self.assertEqual(facts[1].effective_date.isoformat(), "2026-06-01")
        self.assertEqual(facts[2].related_event_id, "event_01")


class RealMessageEvidenceTests(unittest.TestCase):
    def test_real_messages_produce_typed_facts_without_executing_text(self) -> None:
        root = Path(__file__).resolve().parents[1]
        dataset = load_dataset(root / "dataset")
        facts = extract_message_facts(dataset.messages)
        self.assertGreater(len(facts), 50)
        self.assertEqual(
            {fact.fact_type for fact in facts},
            set(MessageFactType),
        )
        self.assertTrue(all(fact.message_id and fact.evidence_text for fact in facts))
