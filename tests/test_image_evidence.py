"""Step 7 tests for controlled image evidence extraction and caching."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "code"))

from domain import Currency, EventStatus  # noqa: E402
from evidence import (  # noqa: E402
    ImageResolutionStatus,
    VisionEvidence,
    resolve_image_evidence,
)
from ingest import load_dataset  # noqa: E402
from ledger import normalize_ledger_input  # noqa: E402
from tests.test_ingest import fixture_rows, write_fixture  # noqa: E402


class FakeOcr:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def extract_text(self, image_path: Path) -> str:
        self.calls += 1
        return self.text


class FakeVision:
    def __init__(self, evidence: VisionEvidence) -> None:
        self.evidence = evidence
        self.calls = 0

    def extract_evidence(self, image_path: Path) -> VisionEvidence:
        self.calls += 1
        return self.evidence


class ImageEvidenceTests(unittest.TestCase):
    def resolve_fixture(
        self,
        *,
        ocr_text: str | None,
        status: str = "settled",
        direction: str = "debit",
        currency: str = "INR",
        create_image: bool = True,
        vision=None,
        cache_path: Path | None = None,
    ):
        temporary_directory = tempfile.TemporaryDirectory()
        directory = Path(temporary_directory.name)
        self.addCleanup(temporary_directory.cleanup)
        rows = fixture_rows()
        event = rows["financial_events.csv"][0]
        event.update(amount="", status=status, direction=direction, currency=currency)
        write_fixture(directory, rows)
        image_directory = directory / "media" / "images"
        image_directory.mkdir(parents=True)
        if create_image:
            (image_directory / "image_01.png").write_bytes(b"fixture image bytes")
        dataset = load_dataset(directory)
        ocr = FakeOcr(ocr_text) if ocr_text is not None else None
        report = resolve_image_evidence(
            dataset=dataset,
            normalized=normalize_ledger_input(dataset),
            image_directory=image_directory,
            ocr_engine=ocr,
            vision_engine=vision,
            cache_path=cache_path,
        )
        return report, ocr, directory

    def test_resolves_payslip_net_pay_for_a_settled_credit(self) -> None:
        report, _, _ = self.resolve_fixture(
            ocr_text="PAY SLIP\nNet Pay : IDR 4,365,000",
            direction="credit",
            currency="IDR",
        )
        resolution = report.resolutions[0]

        self.assertEqual(resolution.status, ImageResolutionStatus.RESOLVED)
        self.assertEqual(resolution.resolved_amount, Decimal("4365000"))
        self.assertEqual(resolution.extraction_method, "ocr")

    def test_resolves_pending_bill_amount_due(self) -> None:
        report, _, _ = self.resolve_fixture(
            ocr_text="This month's charges\nAmount Due ₹704.05",
            status="pending",
        )
        resolution = report.resolutions[0]

        self.assertEqual(resolution.status, ImageResolutionStatus.RESOLVED)
        self.assertEqual(resolution.resolved_amount, Decimal("704.05"))

    def test_resolves_settled_paid_invoice_total(self) -> None:
        report, _, _ = self.resolve_fixture(ocr_text="Receipt\nTotal paid INR 2,298.00")
        resolution = report.resolutions[0]

        self.assertEqual(resolution.resolved_amount, Decimal("2298.00"))
        self.assertEqual(resolution.reviewer_status, "deterministic_validated")

    def test_ambiguous_ocr_can_use_validated_structured_vision_evidence(self) -> None:
        vision = FakeVision(
            VisionEvidence(
                amount=Decimal("20"),
                currency=Currency.INR,
                effective_date=date(2026, 1, 1),
                document_type="receipt",
                payment_state=EventStatus.SETTLED,
                confidence=Decimal("0.90"),
                evidence_text="Total paid INR 20",
            )
        )
        report, _, _ = self.resolve_fixture(
            ocr_text="Total paid INR 10\nTotal paid INR 20", vision=vision
        )
        resolution = report.resolutions[0]

        self.assertEqual(resolution.status, ImageResolutionStatus.RESOLVED)
        self.assertEqual(resolution.resolved_amount, Decimal("20"))
        self.assertEqual(resolution.extraction_method, "vision")
        self.assertEqual(vision.calls, 1)

    def test_conflicting_structured_vision_evidence_is_blocked(self) -> None:
        vision = FakeVision(
            VisionEvidence(
                amount=Decimal("20"),
                currency=Currency.INR,
                effective_date=date(2027, 1, 1),
                document_type="receipt",
                payment_state=EventStatus.SETTLED,
                confidence=Decimal("0.90"),
                evidence_text="Total paid INR 20",
            )
        )
        report, _, _ = self.resolve_fixture(
            ocr_text="Total paid INR 10\nTotal paid INR 20", vision=vision
        )
        resolution = report.resolutions[0]

        self.assertEqual(resolution.status, ImageResolutionStatus.BLOCKED)
        self.assertIsNone(resolution.resolved_amount)
        self.assertIn("vision date conflicts", resolution.detail)

    def test_missing_image_is_explicitly_conservatively_blocked(self) -> None:
        report, ocr, _ = self.resolve_fixture(ocr_text="Total paid INR 99", create_image=False)
        resolution = report.resolutions[0]

        self.assertEqual(resolution.status, ImageResolutionStatus.BLOCKED)
        self.assertIsNone(resolution.resolved_amount)
        self.assertIn("absent", resolution.detail)
        self.assertEqual(ocr.calls, 0)

    def test_hash_cache_prevents_a_second_ocr_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_path = Path(temporary_directory) / "evidence_cache.json"
            first, first_ocr, directory = self.resolve_fixture(
                ocr_text="Total paid INR 99", cache_path=cache_path
            )
            dataset = load_dataset(directory)
            second_ocr = FakeOcr("should not be read")
            second = resolve_image_evidence(
                dataset=dataset,
                normalized=normalize_ledger_input(dataset),
                image_directory=directory / "media" / "images",
                ocr_engine=second_ocr,
                cache_path=cache_path,
            )

        self.assertEqual(first.resolutions[0].resolved_amount, Decimal("99"))
        self.assertEqual(first_ocr.calls, 1)
        self.assertEqual(second.resolutions[0].resolved_amount, Decimal("99"))
        self.assertEqual(second.resolutions[0].extraction_method, "cache")
        self.assertEqual(second_ocr.calls, 0)


class RealDatasetImageEvidenceTests(unittest.TestCase):
    def test_all_real_blank_amounts_are_audited_when_offline_ocr_is_unavailable(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        dataset = load_dataset(repository_root / "dataset")
        report = resolve_image_evidence(
            dataset=dataset,
            normalized=normalize_ledger_input(dataset),
            image_directory=repository_root / "dataset" / "media" / "images",
            ocr_engine=None,
        )

        self.assertEqual(len(report.resolutions), 16)
        self.assertEqual(len(report.resolved_amounts_by_event), 0)
        self.assertEqual(len(report.blocked_event_ids), 16)
        self.assertTrue(all(item.resolved_amount is None for item in report.resolutions))
