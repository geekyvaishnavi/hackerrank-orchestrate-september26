"""Typed, immutable domain records and exact-value primitives.

This module is deliberately independent of CSV parsing and financial decision
logic. Later layers must convert raw input into these records before using it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from typing import TypeVar


class Currency(str, Enum):
    EUR = "EUR"
    IDR = "IDR"
    INR = "INR"
    USD = "USD"
    ZAR = "ZAR"


class AffordabilityStatus(str, Enum):
    AFFORDABLE_NOW = "affordable_now"
    AFFORDABLE_WITH_PLAN = "affordable_with_plan"
    AFFORDABLE_LATER = "affordable_later"
    NOT_AFFORDABLE = "not_affordable"


class PaymentMethod(str, Enum):
    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"
    WAIT = "wait"
    NOT_RECOMMENDED = "not_recommended"


class EventStatus(str, Enum):
    SETTLED = "settled"
    PENDING = "pending"
    SCHEDULED = "scheduled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNREALIZED = "unrealized"


class Direction(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    NON_CASH = "non_cash"


class Flexibility(str, Enum):
    FIXED = "fixed"
    REDUCIBLE = "reducible"
    STOPPABLE = "stoppable"
    REDUCIBLE_OR_STOPPABLE = "reducible_or_stoppable"


class EventType(str, Enum):
    DEBT_PAYMENT = "debt_payment"
    EXPENSE = "expense"
    INCOME = "income"
    INVESTMENT_PURCHASE = "investment_purchase"
    INVESTMENT_SALE = "investment_sale"
    INVESTMENT_VALUATION = "investment_valuation"
    REFUND = "refund"
    SUBSCRIPTION = "subscription"


class RequestType(str, Enum):
    PURCHASE = "purchase"
    TRAVEL = "travel"
    EDUCATION = "education"
    FAMILY_TRANSFER = "family_transfer"
    DEBT_REPAYMENT = "debt_repayment"
    INVESTMENT = "investment"
    HOUSING = "housing"
    EMERGENCY_EXPENSE = "emergency_expense"
    OTHER = "other"


class CashFlowKind(str, Enum):
    BASELINE = "baseline"
    CONFIRMED_INCOME = "confirmed_income"
    ESSENTIAL_EXPENSE = "essential_expense"
    PENDING_DEBIT = "pending_debit"
    PLAN_PAYMENT = "plan_payment"


EnumType = TypeVar("EnumType", bound=Enum)


def parse_money(value: Decimal | int | str, *, field_name: str = "amount") -> Decimal:
    """Return one finite Decimal, rejecting floats and blank raw values.

    A float can already have lost precision before conversion, so callers must
    supply an integer, decimal string, or Decimal instance instead.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{field_name} must be Decimal, int, or str; floats are forbidden")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError(f"{field_name} cannot be blank")
    if not isinstance(value, (Decimal, int, str)):
        raise TypeError(f"{field_name} must be Decimal, int, or str")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field_name} is not a valid decimal value") from error
    if not result.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return result


def quantize_money(amount: Decimal, *, decimal_places: int = 2) -> Decimal:
    """Round an exact Decimal to an explicit output precision."""
    if not isinstance(amount, Decimal):
        raise TypeError("amount must be a Decimal")
    if isinstance(decimal_places, bool) or not isinstance(decimal_places, int):
        raise TypeError("decimal_places must be an integer")
    if decimal_places < 0:
        raise ValueError("decimal_places must not be negative")
    quantum = Decimal(1).scaleb(-decimal_places)
    return amount.quantize(quantum, rounding=ROUND_HALF_UP)


def format_money(amount: Decimal, *, decimal_places: int | None = None) -> str:
    """Format a Decimal without converting it to float.

    When precision is omitted, retain the amount's source precision. Output
    policy is selected later by the CSV serializer, not by domain records.
    """
    if not isinstance(amount, Decimal):
        raise TypeError("amount must be a Decimal")
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    if decimal_places is not None:
        amount = quantize_money(amount, decimal_places=decimal_places)
    return format(amount, "f")


def parse_date(value: str, *, field_name: str = "date") -> date:
    """Parse an exact `YYYY-MM-DD` date and reject timestamps or loose forms."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date string")
    try:
        result = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format") from error
    if result.isoformat() != value:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format")
    return result


def parse_optional_date(value: str | None, *, field_name: str = "date") -> date | None:
    """Parse an optional external date; use None internally for an empty date."""
    if value is None:
        return None
    return parse_date(value, field_name=field_name)


def _require_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_date(value: date | None, field_name: str, *, optional: bool = False) -> None:
    if value is None:
        if optional:
            return
        raise ValueError(f"{field_name} is required")
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{field_name} must be a date")


def _require_enum(value: Enum, expected_type: type[EnumType], field_name: str) -> None:
    if not isinstance(value, expected_type):
        raise TypeError(f"{field_name} must be a {expected_type.__name__}")


def _require_decimal(value: Decimal | None, field_name: str, *, optional: bool = False) -> None:
    if value is None:
        if optional:
            return
        raise ValueError(f"{field_name} is required")
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal; floats are forbidden")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")


def _require_non_negative(value: Decimal, field_name: str) -> None:
    _require_decimal(value, field_name)
    if value < Decimal("0"):
        raise ValueError(f"{field_name} must not be negative")


@dataclass(frozen=True, slots=True)
class FinancialProfile:
    user_id: str
    home_currency: Currency
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: frozenset[str]
    protected_categories: frozenset[str]
    reducible_categories: frozenset[str]
    stoppable_categories: frozenset[str]
    payment_methods: frozenset[PaymentMethod]
    max_installment_months: int | None

    def __post_init__(self) -> None:
        _require_id(self.user_id, "user_id")
        _require_enum(self.home_currency, Currency, "home_currency")
        _require_non_negative(self.current_available_balance, "current_available_balance")
        _require_non_negative(self.minimum_balance_to_keep, "minimum_balance_to_keep")
        if self.max_installment_months is not None and (
            isinstance(self.max_installment_months, bool)
            or not isinstance(self.max_installment_months, int)
            or self.max_installment_months < 1
        ):
            raise ValueError("max_installment_months must be a positive integer or None")
        if not all(isinstance(method, PaymentMethod) for method in self.payment_methods):
            raise TypeError("payment_methods must contain PaymentMethod values")


@dataclass(frozen=True, slots=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: RequestType
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str

    def __post_init__(self) -> None:
        _require_id(self.request_id, "request_id")
        _require_id(self.user_id, "user_id")
        _require_date(self.request_date, "request_date")
        _require_enum(self.request_type, RequestType, "request_type")
        _require_non_negative(self.requested_amount, "requested_amount")
        _require_date(self.desired_completion_date, "desired_completion_date")
        if self.desired_completion_date < self.request_date:
            raise ValueError("desired_completion_date cannot precede request_date")
        if not isinstance(self.allows_partial_payment, bool):
            raise TypeError("allows_partial_payment must be a bool")


@dataclass(frozen=True, slots=True)
class FinancialEvent:
    event_id: str
    user_id: str
    event_type: EventType
    description: str
    category: str
    direction: Direction
    amount: Decimal | None
    currency: Currency
    event_date: date
    settlement_date: date | None
    status: EventStatus
    linked_event_id: str | None
    flexibility: Flexibility
    minimum_allowed_amount: Decimal | None

    def __post_init__(self) -> None:
        _require_id(self.event_id, "event_id")
        _require_id(self.user_id, "user_id")
        _require_enum(self.event_type, EventType, "event_type")
        _require_enum(self.direction, Direction, "direction")
        _require_decimal(self.amount, "amount", optional=True)
        if self.amount is not None:
            _require_non_negative(self.amount, "amount")
        _require_enum(self.currency, Currency, "currency")
        _require_date(self.event_date, "event_date")
        _require_date(self.settlement_date, "settlement_date", optional=True)
        _require_enum(self.status, EventStatus, "status")
        _require_enum(self.flexibility, Flexibility, "flexibility")
        _require_decimal(self.minimum_allowed_amount, "minimum_allowed_amount", optional=True)
        if self.minimum_allowed_amount is not None:
            _require_non_negative(self.minimum_allowed_amount, "minimum_allowed_amount")


@dataclass(frozen=True, slots=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: PaymentMethod
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: int | None
    financing_fee: Decimal
    total_payable_amount: Decimal

    def __post_init__(self) -> None:
        _require_id(self.payment_option_id, "payment_option_id")
        _require_id(self.request_id, "request_id")
        _require_enum(self.payment_method, PaymentMethod, "payment_method")
        _require_non_negative(self.payment_amount, "payment_amount")
        if (
            isinstance(self.number_of_payments, bool)
            or not isinstance(self.number_of_payments, int)
            or self.number_of_payments < 1
        ):
            raise ValueError("number_of_payments must be a positive integer")
        _require_date(self.first_payment_date, "first_payment_date")
        if self.payment_frequency_days is not None and (
            isinstance(self.payment_frequency_days, bool)
            or not isinstance(self.payment_frequency_days, int)
            or self.payment_frequency_days < 1
        ):
            raise ValueError("payment_frequency_days must be a positive integer or None")
        _require_non_negative(self.financing_fee, "financing_fee")
        _require_non_negative(self.total_payable_amount, "total_payable_amount")


@dataclass(frozen=True, slots=True)
class Message:
    message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_at: datetime
    source_type: str
    message_text: str

    def __post_init__(self) -> None:
        _require_id(self.message_id, "message_id")
        _require_id(self.user_id, "user_id")
        if self.request_id is not None:
            _require_id(self.request_id, "request_id")
        if self.related_event_id is not None:
            _require_id(self.related_event_id, "related_event_id")
        if not isinstance(self.sent_at, datetime):
            raise TypeError("sent_at must be a datetime")
        _require_id(self.source_type, "source_type")


@dataclass(frozen=True, slots=True)
class ImageReference:
    image_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None

    def __post_init__(self) -> None:
        _require_id(self.image_id, "image_id")
        _require_id(self.user_id, "user_id")
        if self.request_id is not None:
            _require_id(self.request_id, "request_id")
        if self.related_event_id is not None:
            _require_id(self.related_event_id, "related_event_id")


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    rate_date: date
    from_currency: Currency
    to_currency: Currency
    rate: Decimal

    def __post_init__(self) -> None:
        _require_date(self.rate_date, "rate_date")
        _require_enum(self.from_currency, Currency, "from_currency")
        _require_enum(self.to_currency, Currency, "to_currency")
        _require_decimal(self.rate, "rate")
        if self.rate <= Decimal("0"):
            raise ValueError("rate must be positive")


@dataclass(frozen=True, slots=True)
class ExtractedEvidence:
    source_id: str
    amount: Decimal | None
    currency: Currency | None
    effective_date: date | None
    payment_state: EventStatus | None
    confidence: Decimal
    evidence_text: str

    def __post_init__(self) -> None:
        _require_id(self.source_id, "source_id")
        _require_decimal(self.amount, "amount", optional=True)
        if self.amount is not None:
            _require_non_negative(self.amount, "amount")
        if self.currency is not None:
            _require_enum(self.currency, Currency, "currency")
        _require_date(self.effective_date, "effective_date", optional=True)
        if self.payment_state is not None:
            _require_enum(self.payment_state, EventStatus, "payment_state")
        _require_decimal(self.confidence, "confidence")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class CashFlowItem:
    cash_date: date
    amount: Decimal
    currency: Currency
    kind: CashFlowKind
    source_id: str

    def __post_init__(self) -> None:
        _require_date(self.cash_date, "cash_date")
        _require_decimal(self.amount, "amount")
        _require_enum(self.currency, Currency, "currency")
        _require_enum(self.kind, CashFlowKind, "kind")
        _require_id(self.source_id, "source_id")


@dataclass(frozen=True, slots=True)
class ScheduledPayment:
    payment_date: date
    amount: Decimal

    def __post_init__(self) -> None:
        _require_date(self.payment_date, "payment_date")
        _require_non_negative(self.amount, "amount")


@dataclass(frozen=True, slots=True)
class CandidatePlan:
    method: PaymentMethod
    payments: tuple[ScheduledPayment, ...]
    total_payable_amount: Decimal
    payment_option_id: str | None
    spending_changes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_enum(self.method, PaymentMethod, "method")
        if not self.payments:
            raise ValueError("payments must not be empty")
        if not all(isinstance(payment, ScheduledPayment) for payment in self.payments):
            raise TypeError("payments must contain ScheduledPayment values")
        if tuple(sorted(payment.payment_date for payment in self.payments)) != tuple(
            payment.payment_date for payment in self.payments
        ):
            raise ValueError("payments must be chronological")
        _require_non_negative(self.total_payable_amount, "total_payable_amount")
        if self.payment_option_id is not None:
            _require_id(self.payment_option_id, "payment_option_id")


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    forecast_date: date
    closing_balance: Decimal

    def __post_init__(self) -> None:
        _require_date(self.forecast_date, "forecast_date")
        _require_decimal(self.closing_balance, "closing_balance")


@dataclass(frozen=True, slots=True)
class ForecastResult:
    points: tuple[ForecastPoint, ...]
    minimum_balance: Decimal
    first_breach_date: date | None

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("points must not be empty")
        if not all(isinstance(point, ForecastPoint) for point in self.points):
            raise TypeError("points must contain ForecastPoint values")
        _require_decimal(self.minimum_balance, "minimum_balance")
        _require_date(self.first_breach_date, "first_breach_date", optional=True)


@dataclass(frozen=True, slots=True)
class Decision:
    request_id: str
    amount_safe_to_pay: Decimal
    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod
    payment_plan: tuple[ScheduledPayment, ...]
    earliest_date_for_full_payment: date | None
    spending_changes_needed: tuple[str, ...]
    decision_explanation: str

    def __post_init__(self) -> None:
        _require_id(self.request_id, "request_id")
        _require_non_negative(self.amount_safe_to_pay, "amount_safe_to_pay")
        _require_enum(self.affordability_status, AffordabilityStatus, "affordability_status")
        _require_enum(
            self.recommended_payment_method, PaymentMethod, "recommended_payment_method"
        )
        if not all(isinstance(payment, ScheduledPayment) for payment in self.payment_plan):
            raise TypeError("payment_plan must contain ScheduledPayment values")
        _require_date(
            self.earliest_date_for_full_payment,
            "earliest_date_for_full_payment",
            optional=True,
        )
        if not isinstance(self.decision_explanation, str) or not self.decision_explanation.strip():
            raise ValueError("decision_explanation must be non-empty")
