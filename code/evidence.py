"""Controlled image evidence extraction for financial events with blank amounts."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
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


class MessageFactType(str, Enum):
    SALARY_AMOUNT = "salary_amount"
    CONFIRMED_INCOME = "confirmed_income"
    INCOME_ENDED = "income_ended"
    RENT_INCREASE = "rent_increase"
    PENDING_CREDIT = "pending_credit"
    FAILED_DEBIT_RETRY = "failed_debit_retry"
    EVENT_SETTLED = "event_settled"
    INTERNAL_TRANSFER = "internal_transfer"


@dataclass(frozen=True, slots=True)
class MessageFact:
    """A typed financial fact extracted from a message, never an instruction."""

    message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_at: datetime
    source_type: str
    fact_type: MessageFactType
    amount: Decimal | None
    currency: Currency | None
    effective_date: date | None
    percentage: Decimal | None
    evidence_text: str

    def __post_init__(self) -> None:
        if not self.message_id or not self.user_id:
            raise ValueError("message facts require message and user IDs")
        if not isinstance(self.sent_at, datetime):
            raise TypeError("message fact sent_at must be a datetime")
        if self.amount is not None and (
            not isinstance(self.amount, Decimal) or not self.amount.is_finite() or self.amount < Decimal("0")
        ):
            raise ValueError("message fact amount must be a non-negative finite Decimal")
        if self.currency is not None and not isinstance(self.currency, Currency):
            raise TypeError("message fact currency must be a Currency or None")
        if self.amount is not None and self.currency is None:
            raise ValueError("message fact amount requires a currency")
        if self.effective_date is not None and not isinstance(self.effective_date, date):
            raise TypeError("message fact effective_date must be a date or None")
        if self.percentage is not None and (
            not isinstance(self.percentage, Decimal)
            or not self.percentage.is_finite()
            or self.percentage < Decimal("0")
        ):
            raise ValueError("message fact percentage must be a non-negative finite Decimal")
        if not self.evidence_text:
            raise ValueError("message facts require quoted source evidence")


def _message_amount(text: str) -> tuple[Decimal, Currency] | None:
    match = re.search(r"\b(IDR|INR|USD|EUR|ZAR)\s*([\d,]+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if match is None:
        return None
    return parse_money(match.group(2).replace(",", ""), field_name="message amount"), Currency(match.group(1).upper())


def _message_date(text: str) -> date | None:
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    return date.fromisoformat(match.group(1)) if match else None


def _fact(message, fact_type: MessageFactType, *, amount=None, currency=None, effective_date=None, percentage=None) -> MessageFact:
    return MessageFact(
        message_id=message.message_id,
        user_id=message.user_id,
        request_id=message.request_id,
        related_event_id=message.related_event_id,
        sent_at=message.sent_at,
        source_type=message.source_type,
        fact_type=fact_type,
        amount=amount,
        currency=currency,
        effective_date=effective_date,
        percentage=percentage,
        evidence_text=message.message_text,
    )


def extract_message_facts(messages: Sequence) -> tuple[MessageFact, ...]:
    """Extract only supported facts from English/Indonesian source messages.

    Calls to pay, release fees, or otherwise act are deliberately not facts and
    are ignored even when included in a trusted-source-looking message.
    """
    facts: list[MessageFact] = []
    for message in messages:
        text = message.message_text
        lower = text.casefold()
        amount_currency = _message_amount(text)
        effective_date = _message_date(text)
        if "matching debit and credit" in lower or "transfer between your two accounts" in lower:
            facts.append(_fact(message, MessageFactType.INTERNAL_TRANSFER))
        if "failed" in lower and ("another debit" in lower or "bill is still outstanding" in lower):
            facts.append(_fact(message, MessageFactType.FAILED_DEBIT_RETRY))
        if ("refund has been initiated" in lower or "payment processing" in lower) and (
            "has not reached" in lower or "not been credited" in lower
        ):
            facts.append(_fact(message, MessageFactType.PENDING_CREDIT))
        if "received on" in lower or "confirmed that" in lower and "was paid" in lower:
            if message.related_event_id:
                facts.append(_fact(message, MessageFactType.EVENT_SETTLED))
        if (
            "employment has ended" in lower
            or "employment record has ended" in lower
            or "contract has ended" in lower
            or ("hubungan kerja" in lower and "berakhir" in lower)
        ):
            facts.append(_fact(message, MessageFactType.INCOME_ENDED, effective_date=effective_date))
        if "increases monthly rent by" in lower or "meningkatkan sewa" in lower:
            percent = re.search(r"(\d+(?:\.\d+)?)%", text)
            if percent:
                facts.append(
                    _fact(
                        message,
                        MessageFactType.RENT_INCREASE,
                        percentage=parse_money(percent.group(1)) / Decimal("100"),
                        effective_date=effective_date,
                    )
                )
        salary_markers = ("salary", "gaji", "payroll")
        is_salary = any(marker in lower for marker in salary_markers)
        if is_salary and amount_currency and (
            "increased to" in lower
            or "monthly pay is" in lower
            or "next salary is reduced to" in lower
            or "confirmed base salary" in lower
            or "gaji bulanan anda naik menjadi" in lower
            or "gaji bulanan sementara" in lower
        ):
            amount, currency = amount_currency
            facts.append(
                _fact(
                    message,
                    MessageFactType.SALARY_AMOUNT,
                    amount=amount,
                    currency=currency,
                    effective_date=effective_date,
                )
            )
        if amount_currency and (
            "salary of" in lower and "confirmed" in lower
            or "first salary" in lower and ("confirmed" in lower or "scheduled" in lower)
            or "invoice payment" in lower and ("approved" in lower or "confirmed" in lower)
            or "gaji pertama" in lower and "dijadwalkan" in lower
        ):
            amount, currency = amount_currency
            facts.append(
                _fact(
                    message,
                    MessageFactType.CONFIRMED_INCOME,
                    amount=amount,
                    currency=currency,
                    effective_date=effective_date,
                )
            )
    return tuple(facts)


def resolve_message_conflicts(facts: Sequence[MessageFact]) -> tuple[MessageFact, ...]:
    """Choose one fact per scope/type, preferring explicit settlement then newer source facts."""
    grouped: dict[tuple[str, str | None, MessageFactType], list[MessageFact]] = {}
    for fact in facts:
        key = (fact.user_id, fact.related_event_id, fact.fact_type)
        grouped.setdefault(key, []).append(fact)
    resolved: list[MessageFact] = []
    for entries in grouped.values():
        resolved.append(
            max(
                entries,
                key=lambda fact: (
                    fact.fact_type is MessageFactType.EVENT_SETTLED,
                    fact.sent_at,
                    fact.message_id,
                ),
            )
        )
    return tuple(sorted(resolved, key=lambda fact: (fact.sent_at, fact.message_id)))


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
