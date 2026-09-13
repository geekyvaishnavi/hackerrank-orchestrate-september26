"""Controlled image evidence extraction for financial events with blank amounts."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from domain import Currency, Direction, EventStatus, ImageReference, parse_money
from ingest import IngestedDataset
from ledger import CanonicalEvent, NormalizedLedgerInput


class ImageResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    BLOCKED = "blocked"


class OcrEngine(Protocol):
    """Local OCR interface; implementations must return text only."""

    def extract_text(self, image_path: Path) -> str: ...


class VisionEngine(Protocol):
    """Optional structured-vision interface for OCR ambiguity only."""

    def extract_evidence(self, image_path: Path) -> "VisionEvidence": ...


@dataclass(frozen=True, slots=True)
class VisionEvidence:
    """Strict structured result accepted from an optional vision provider."""

    amount: Decimal
    currency: Currency
    effective_date: date | None
    document_type: str
    payment_state: EventStatus
    confidence: Decimal
    evidence_text: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal) or self.amount < Decimal("0"):
            raise ValueError("vision amount must be a non-negative Decimal")
        if not isinstance(self.currency, Currency):
            raise TypeError("vision currency must be a Currency")
        if self.effective_date is not None and not isinstance(self.effective_date, date):
            raise TypeError("vision effective_date must be a date or None")
        if not isinstance(self.payment_state, EventStatus):
            raise TypeError("vision payment_state must be an EventStatus")
        if (
            not isinstance(self.confidence, Decimal)
            or not self.confidence.is_finite()
            or not Decimal("0") <= self.confidence <= Decimal("1")
        ):
            raise ValueError("vision confidence must be a Decimal from 0 to 1")


@dataclass(frozen=True, slots=True)
class ImageAmountResolution:
    image_id: str | None
    event_id: str
    image_sha256: str | None
    status: ImageResolutionStatus
    resolved_amount: Decimal | None
    currency: Currency
    extraction_method: str
    reviewer_status: str
    evidence_text: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class ImageEvidenceReport:
    resolutions: tuple[ImageAmountResolution, ...]

    @property
    def resolved_amounts_by_event(self) -> Mapping[str, Decimal]:
        return {
            resolution.event_id: resolution.resolved_amount
            for resolution in self.resolutions
            if resolution.status is ImageResolutionStatus.RESOLVED
            and resolution.resolved_amount is not None
        }

    @property
    def blocked_event_ids(self) -> tuple[str, ...]:
        return tuple(
            resolution.event_id
            for resolution in self.resolutions
            if resolution.status is ImageResolutionStatus.BLOCKED
        )

    def render(self) -> str:
        return "\n".join(
            (
                "Image evidence extraction completed.",
                f"Resolved amounts: {len(self.resolved_amounts_by_event)}",
                f"Conservatively blocked: {len(self.blocked_event_ids)}",
            )
        )


class TesseractOcrEngine:
    """Optional local OCR adapter. It has no network or model-provider dependency."""

    def __init__(self, executable: str = "tesseract") -> None:
        if shutil.which(executable) is None:
            raise RuntimeError("tesseract executable is not available")
        self._executable = executable

    def extract_text(self, image_path: Path) -> str:
        result = subprocess.run(
            [self._executable, str(image_path), "stdout"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"local OCR failed: {result.stderr.strip()}")
        return result.stdout


def available_local_ocr() -> OcrEngine | None:
    """Return local OCR when installed; absence is a safe, expected offline state."""
    try:
        return TesseractOcrEngine()
    except RuntimeError:
        return None


class OcrTextCache:
    """Content-addressed OCR text cache; it stores no credentials or model calls."""

    VERSION = "image-ocr-v1"

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._entries: dict[str, str] = {}
        if path is not None and path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") == self.VERSION:
                self._entries = {
                    digest: value
                    for digest, value in payload.get("ocr_text_by_sha256", {}).items()
                    if isinstance(digest, str) and isinstance(value, str)
                }

    def get(self, digest: str) -> str | None:
        return self._entries.get(digest)

    def put(self, digest: str, text: str) -> None:
        self._entries[digest] = text
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(
                    {"version": self.VERSION, "ocr_text_by_sha256": self._entries},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )


_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
_AMOUNT_PATTERN = re.compile(rf"(?:₹|\$|€|R\s*|IDR\s*|INR\s*|USD\s*|EUR\s*|ZAR\s*)?({_NUMBER})", re.IGNORECASE)


def _currency_from_text(text: str) -> Currency | None:
    upper = text.upper()
    for token, currency in (
        ("IDR", Currency.IDR),
        ("INR", Currency.INR),
        ("USD", Currency.USD),
        ("EUR", Currency.EUR),
        ("ZAR", Currency.ZAR),
    ):
        if token in upper:
            return currency
    if "₹" in text:
        return Currency.INR
    if "$" in text:
        return Currency.USD
    if "€" in text:
        return Currency.EUR
    return None


def _amount_from_line(line: str) -> Decimal | None:
    matches = _AMOUNT_PATTERN.findall(line)
    if not matches:
        return None
    try:
        return parse_money(matches[-1].replace(",", ""), field_name="OCR amount")
    except ValueError:
        return None


def _field_priority(event: CanonicalEvent, line: str) -> int | None:
    text = line.casefold()
    if event.direction is Direction.DEBIT and event.status in {EventStatus.PENDING, EventStatus.SCHEDULED}:
        labels = ("balance due", "amount due", "amount payable", "amount outstanding")
        if any(label in text for label in labels):
            return 0
        if "total" in text:
            return 2
        return None
    if event.direction is Direction.CREDIT:
        labels = ("net pay", "amount received", "total amount received", "grand total")
    else:
        labels = ("total paid", "cash paid", "amount received", "grand total", "net amount", "total amount")
    for priority, label in enumerate(labels):
        if label in text:
            return priority
    if re.search(r"\btotal\b", text):
        return len(labels) + 1
    return None


def _select_ocr_amount(event: CanonicalEvent, ocr_text: str) -> tuple[Decimal | None, str | None, str]:
    candidates: list[tuple[int, Decimal, str]] = []
    for line in ocr_text.splitlines():
        priority = _field_priority(event, line)
        amount = _amount_from_line(line)
        if priority is not None and amount is not None:
            candidates.append((priority, amount, line.strip()))
    if not candidates:
        return None, None, "no recognized total/due/paid field"
    best_priority = min(priority for priority, _, _ in candidates)
    best = [(amount, line) for priority, amount, line in candidates if priority == best_priority]
    unique_amounts = {amount for amount, _ in best}
    if len(unique_amounts) != 1:
        return None, None, "ambiguous OCR fields at the same priority"
    amount, line = best[0]
    inferred_currency = _currency_from_text(line) or _currency_from_text(ocr_text)
    if inferred_currency is not None and inferred_currency is not event.currency:
        return None, None, "OCR currency conflicts with event currency"
    return amount, line, "deterministic OCR field match"


def _validate_vision_evidence(event: CanonicalEvent, evidence: VisionEvidence) -> tuple[bool, str]:
    if evidence.currency is not event.currency:
        return False, "vision currency conflicts with event currency"
    if evidence.payment_state is not event.status:
        return False, "vision payment state conflicts with event status"
    if evidence.effective_date is not None and evidence.effective_date not in {
        event.event_date,
        event.cash_date,
    }:
        return False, "vision date conflicts with event date"
    if evidence.confidence < Decimal("0.50"):
        return False, "vision confidence is below 0.50"
    return True, "validated structured vision evidence"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _blocked(
    *, image: ImageReference | None, event: CanonicalEvent, digest: str | None, detail: str
) -> ImageAmountResolution:
    return ImageAmountResolution(
        image_id=image.image_id if image else None,
        event_id=event.event_id,
        image_sha256=digest,
        status=ImageResolutionStatus.BLOCKED,
        resolved_amount=None,
        currency=event.currency,
        extraction_method="blocked",
        reviewer_status="conservative_block",
        evidence_text=None,
        detail=detail,
    )


def resolve_image_evidence(
    *,
    dataset: IngestedDataset,
    normalized: NormalizedLedgerInput,
    image_directory: Path,
    ocr_engine: OcrEngine | None,
    vision_engine: VisionEngine | None = None,
    cache_path: Path | None = None,
) -> ImageEvidenceReport:
    """Resolve only image-linked canonical events whose source amount is blank.

    Missing files, unavailable OCR, ambiguous output, or invalid vision evidence
    produce an explicit blocked record. They never become zero-value events.
    """
    images_by_event: dict[str, list[ImageReference]] = {}
    for image in dataset.images:
        if image.related_event_id:
            images_by_event.setdefault(image.related_event_id, []).append(image)
    cache = OcrTextCache(cache_path)
    resolutions: list[ImageAmountResolution] = []
    for event in normalized.events:
        if event.source_amount is not None:
            continue
        images = images_by_event.get(event.event_id, [])
        if len(images) != 1:
            resolutions.append(
                _blocked(
                    image=images[0] if images else None,
                    event=event,
                    digest=None,
                    detail="expected exactly one image linked to blank-amount event",
                )
            )
            continue
        image = images[0]
        path = image_directory / f"{image.image_id}.png"
        if not path.is_file():
            resolutions.append(_blocked(image=image, event=event, digest=None, detail="image file is absent"))
            continue
        digest = _sha256(path)
        ocr_text = cache.get(digest)
        method = "cache"
        if ocr_text is None:
            if ocr_engine is None:
                resolutions.append(
                    _blocked(image=image, event=event, digest=digest, detail="local OCR is unavailable")
                )
                continue
            try:
                ocr_text = ocr_engine.extract_text(path)
            except Exception as error:  # OCR provider errors are evidence failures, not financial facts.
                resolutions.append(
                    _blocked(image=image, event=event, digest=digest, detail=f"local OCR failed: {error}")
                )
                continue
            cache.put(digest, ocr_text)
            method = "ocr"
        amount, evidence_line, detail = _select_ocr_amount(event, ocr_text)
        if amount is not None:
            resolutions.append(
                ImageAmountResolution(
                    image_id=image.image_id,
                    event_id=event.event_id,
                    image_sha256=digest,
                    status=ImageResolutionStatus.RESOLVED,
                    resolved_amount=amount,
                    currency=event.currency,
                    extraction_method=method,
                    reviewer_status="deterministic_validated",
                    evidence_text=evidence_line,
                    detail=detail,
                )
            )
            continue
        if vision_engine is not None:
            try:
                vision_evidence = vision_engine.extract_evidence(path)
                valid, vision_detail = _validate_vision_evidence(event, vision_evidence)
            except Exception as error:
                valid, vision_detail, vision_evidence = False, f"vision extraction failed: {error}", None
            if valid and vision_evidence is not None:
                resolutions.append(
                    ImageAmountResolution(
                        image_id=image.image_id,
                        event_id=event.event_id,
                        image_sha256=digest,
                        status=ImageResolutionStatus.RESOLVED,
                        resolved_amount=vision_evidence.amount,
                        currency=event.currency,
                        extraction_method="vision",
                        reviewer_status="structured_validated",
                        evidence_text=vision_evidence.evidence_text,
                        detail=vision_detail,
                    )
                )
                continue
            detail = f"{detail}; {vision_detail}"
        resolutions.append(_blocked(image=image, event=event, digest=digest, detail=detail))
    return ImageEvidenceReport(tuple(resolutions))
