"""Step 4 tests for relationship indexes, contexts, and aggregate audits."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from ingest import DataValidationError, load_dataset  # noqa: E402
from relationships import build_relationship_graph  # noqa: E402
from tests.test_ingest import fixture_rows, write_fixture  # noqa: E402


def add_second_evaluation_option(rows: dict[str, list[dict[str, str]]]) -> None:
    second_option = rows["request_payment_options.csv"][0].copy()
    second_option.update(
        payment_option_id="payment_option_02",
        payment_method="installments",
        payment_amount="155.00",
        number_of_payments="2",
        first_payment_date="2026-01-11",
        payment_frequency_days="30",
        financing_fee="9.75",
        total_payable_amount="310.00",
    )
    rows["request_payment_options.csv"].append(second_option)


class RelationshipFixtureTests(unittest.TestCase):
    def load_graph(self, mutate=None):  # type: ignore[no-untyped-def]
        temporary_directory = tempfile.TemporaryDirectory()
        directory = Path(temporary_directory.name)
        rows = fixture_rows()
        add_second_evaluation_option(rows)
        if mutate:
            mutate(rows)
        write_fixture(directory, rows)
        self.addCleanup(temporary_directory.cleanup)
        return build_relationship_graph(load_dataset(directory))

    def test_request_context_returns_only_linked_records(self) -> None:
        graph = self.load_graph()
        context = graph.request_context("request_01")

        self.assertEqual(context.request.request_id, "request_01")
        self.assertEqual(context.profile.user_id, "user_01")
        self.assertEqual(tuple(event.event_id for event in context.user_events), ("event_01",))
        self.assertEqual(len(context.payment_options), 2)
        self.assertEqual(tuple(message.message_id for message in context.request_messages), ("message_01",))
        self.assertEqual(tuple(image.image_id for image in context.request_images), ("image_01",))
        self.assertEqual(tuple(context.event_messages), ("event_01",))
        self.assertEqual(tuple(context.event_images), ("event_01",))

    def test_sample_linked_records_remain_separate_from_evaluation_context(self) -> None:
        def add_sample_records(rows: dict[str, list[dict[str, str]]]) -> None:
            option = rows["request_payment_options.csv"][0].copy()
            option.update(payment_option_id="sample_option_01", request_id="sample_request_01")
            rows["request_payment_options.csv"].append(option)
            message = rows["messages.csv"][0].copy()
            message.update(message_id="sample_message_01", request_id="sample_request_01", related_event_id="")
            rows["messages.csv"].append(message)

        graph = self.load_graph(add_sample_records)
        context = graph.request_context("request_01")

        self.assertEqual(len(context.payment_options), 2)
        self.assertEqual(tuple(message.message_id for message in context.request_messages), ("message_01",))
        self.assertEqual(
            tuple(message.message_id for message in graph.messages_by_request["sample_request_01"]),
            ("sample_message_01",),
        )

    def test_request_context_is_isolated_to_its_user(self) -> None:
        def add_second_user(rows: dict[str, list[dict[str, str]]]) -> None:
            profile = rows["financial_profiles.csv"][0].copy()
            profile.update(user_id="user_02")
            rows["financial_profiles.csv"].append(profile)
            event = rows["financial_events.csv"][0].copy()
            event.update(event_id="event_02", user_id="user_02")
            rows["financial_events.csv"].append(event)
            request = rows["requests.csv"][0].copy()
            request.update(request_id="request_02", user_id="user_02")
            rows["requests.csv"].append(request)
            rows["output.csv"].append({**rows["output.csv"][0], "request_id": "request_02"})
            for suffix in ("03", "04"):
                option = rows["request_payment_options.csv"][0].copy()
                option.update(payment_option_id=f"payment_option_{suffix}", request_id="request_02")
                rows["request_payment_options.csv"].append(option)

        graph = self.load_graph(add_second_user)
        context = graph.request_context("request_01")

        self.assertEqual(tuple(event.user_id for event in context.user_events), ("user_01",))
        self.assertNotIn("request_02", graph.messages_by_request)
        self.assertEqual(tuple(graph.evaluation_requests_by_user), ("user_01", "user_02"))

    def test_blank_relationship_ids_remain_user_only_evidence(self) -> None:
        def add_user_only_message(rows: dict[str, list[dict[str, str]]]) -> None:
            message = rows["messages.csv"][0].copy()
            message.update(message_id="message_02", request_id="", related_event_id="")
            rows["messages.csv"].append(message)

        graph = self.load_graph(add_user_only_message)
        context = graph.request_context("request_01")

        self.assertEqual(tuple(message.message_id for message in context.request_messages), ("message_01",))
        self.assertEqual(tuple(message.message_id for message in context.user_messages), ("message_01", "message_02"))
        self.assertEqual(graph.audit.user_only_message_count, 1)

    def test_rejects_evaluation_request_outside_option_cardinality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            write_fixture(directory)
            dataset = load_dataset(directory)
            with self.assertRaisesRegex(DataValidationError, "must have 2 to 4 payment options"):
                build_relationship_graph(dataset)

    def test_audit_contains_counts_but_not_message_text(self) -> None:
        graph = self.load_graph()
        rendered = graph.audit.render()

        self.assertIn("2 options: 1 requests", rendered)
        self.assertIn("Request-linked messages: 1", rendered)
        self.assertNotIn("Payment settled.", rendered)

    def test_unknown_request_context_is_rejected(self) -> None:
        graph = self.load_graph()
        with self.assertRaisesRegex(KeyError, "unknown evaluation request_id"):
            graph.request_context("sample_request_01")


class RelationshipRealDatasetTests(unittest.TestCase):
    def test_real_dataset_builds_a_complete_relationship_graph(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        graph = build_relationship_graph(load_dataset(repository_root / "dataset"))
        context = graph.request_context("request_26")

        self.assertEqual(graph.audit.evaluation_request_count, 250)
        self.assertEqual(sum(graph.audit.option_count_histogram.values()), 250)
        self.assertTrue(all(2 <= count <= 4 for count in graph.audit.option_count_histogram))
        self.assertEqual(context.request.user_id, context.profile.user_id)
        self.assertTrue(2 <= len(context.payment_options) <= 4)
