"""Canonical, source-currency normalization for events and payment options.

This module deliberately does not decide whether a record counts as spendable
cash, convert currencies, forecast balances, or select a recommendation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Sequence, TypeVar

from currency import ConversionAudit, ConversionStatus, CurrencyConverter
from evidence import MessageFact, MessageFactType, resolve_message_conflicts

from domain import (
    Currency,
    Direction,
    EventStatus,
    EventType,
    FinancialEvent,
    FinancialProfile,
    Flexibility,
    PaymentMethod,
    PaymentOption,
)
from ingest import IngestedDataset


RecordType = TypeVar("RecordType")


def normalize_category(category: str) -> str:
    """Return a stable category key shared by events and profile preferences."""
    if not isinstance(category, str):
        raise TypeError("category must be a string")
    normalized = re.sub(r"[_\s-]+", "_", category.strip().casefold())
    if not normalized:
        raise ValueError("category cannot be blank")
    return normalized


@dataclass(frozen=True, slots=True)
class NormalizedProfilePreferences:
    """Profile settings with all category-like values reduced to stable keys."""

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


@dataclass(frozen=True, slots=True)
class CanonicalEvent:
    """An event normalized for later FX conversion and ledger reconstruction."""

    event_id: str
    user_id: str
    event_type: EventType
    description: str
    category: str
    direction: Direction
    source_amount: Decimal | None
    signed_source_amount: Decimal | None
    currency: Currency
    event_date: date
    settlement_date: date | None
    cash_date: date
    status: EventStatus
    linked_event_id: str | None
    flexibility: Flexibility
    minimum_allowed_source_amount: Decimal | None


@dataclass(frozen=True, slots=True)
class PaymentScheduleSpec:
    """Supplied payment cadence; dates are expanded only in the plan-generation step."""

    first_payment_date: date
    number_of_payments: int
    payment_frequency_days: int | None

    @property
    def is_one_time(self) -> bool:
        return self.number_of_payments == 1


@dataclass(frozen=True, slots=True)
class CanonicalPaymentOption:
    """A provider option preserving all source values and its payment schedule."""

    payment_option_id: str
    request_id: str
    payment_method: PaymentMethod
    payment_amount: Decimal
    schedule: PaymentScheduleSpec
    financing_fee: Decimal
    total_payable_amount: Decimal


@dataclass(frozen=True, slots=True)
class NormalizedLedgerInput:
    """Canonical source-currency inputs indexed for later, non-mutating steps."""

    preferences_by_user: Mapping[str, NormalizedProfilePreferences]
    events: tuple[CanonicalEvent, ...]
    events_by_id: Mapping[str, CanonicalEvent]
    events_by_user: Mapping[str, tuple[CanonicalEvent, ...]]
    payment_options: tuple[CanonicalPaymentOption, ...]
    payment_options_by_id: Mapping[str, CanonicalPaymentOption]
    payment_options_by_request: Mapping[str, tuple[CanonicalPaymentOption, ...]]


@dataclass(frozen=True, slots=True)
class EffectiveCashRecord:
    """One dated home-currency cash effect retained by the conservative ledger."""

    source_id: str
    cash_date: date
    amount: Decimal
    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class LedgerAuditEntry:
    """Explain why an event or message was included, reserved, or excluded."""

    source_id: str
    disposition: str
    detail: str
    conversion: ConversionAudit | None = None


@dataclass(frozen=True, slots=True)
class EffectiveFinancialState:
    """Read-only state for one user at one request date; no affordability decision."""

    user_id: str
    as_of_date: date
    opening_available_balance: Decimal
    home_currency: Currency
    cash_flows: tuple[EffectiveCashRecord, ...]
    reserved_obligations: tuple[EffectiveCashRecord, ...]
    audit: tuple[LedgerAuditEntry, ...]

    @property
    def dated_net_cash_flow(self) -> Mapping[date, Decimal]:
        totals: dict[date, Decimal] = {}
        for item in self.cash_flows + self.reserved_obligations:
            totals[item.cash_date] = totals.get(item.cash_date, Decimal("0")) + item.amount
        return MappingProxyType(dict(sorted(totals.items())))


def normalize_profile_preferences(profile: FinancialProfile) -> NormalizedProfilePreferences:
    """Normalize profile category sets without altering amounts or eligibility."""
    return NormalizedProfilePreferences(
        user_id=profile.user_id,
        home_currency=profile.home_currency,
        current_available_balance=profile.current_available_balance,
        minimum_balance_to_keep=profile.minimum_balance_to_keep,
        financial_priorities=frozenset(normalize_category(item) for item in profile.financial_priorities),
        protected_categories=frozenset(normalize_category(item) for item in profile.protected_categories),
        reducible_categories=frozenset(normalize_category(item) for item in profile.reducible_categories),
        stoppable_categories=frozenset(normalize_category(item) for item in profile.stoppable_categories),
        payment_methods=profile.payment_methods,
        max_installment_months=profile.max_installment_months,
    )


def normalize_event(event: FinancialEvent) -> CanonicalEvent:
    """Resolve canonical event fields while retaining every raw cash-state signal."""
    if event.direction is Direction.NON_CASH or event.amount is None:
        signed_amount = None
    elif event.direction is Direction.DEBIT:
        signed_amount = -event.amount
    else:
        signed_amount = event.amount
    return CanonicalEvent(
        event_id=event.event_id,
        user_id=event.user_id,
        event_type=event.event_type,
        description=event.description,
        category=normalize_category(event.category),
        direction=event.direction,
        source_amount=event.amount,
        signed_source_amount=signed_amount,
        currency=event.currency,
        event_date=event.event_date,
        settlement_date=event.settlement_date,
        cash_date=event.settlement_date or event.event_date,
        status=event.status,
        linked_event_id=event.linked_event_id,
        flexibility=event.flexibility,
        minimum_allowed_source_amount=event.minimum_allowed_amount,
    )


def normalize_payment_option(option: PaymentOption) -> CanonicalPaymentOption:
    """Preserve provider option amounts and make its cadence explicit."""
    return CanonicalPaymentOption(
        payment_option_id=option.payment_option_id,
        request_id=option.request_id,
        payment_method=option.payment_method,
        payment_amount=option.payment_amount,
        schedule=PaymentScheduleSpec(
            first_payment_date=option.first_payment_date,
            number_of_payments=option.number_of_payments,
            payment_frequency_days=option.payment_frequency_days,
        ),
        financing_fee=option.financing_fee,
        total_payable_amount=option.total_payable_amount,
    )


def _group_by(
    records: Sequence[RecordType], attribute: str
) -> Mapping[str, tuple[RecordType, ...]]:
    groups: dict[str, list[RecordType]] = defaultdict(list)
    for record in records:
        groups[getattr(record, attribute)].append(record)
    return MappingProxyType({key: tuple(value) for key, value in groups.items()})


def normalize_ledger_input(dataset: IngestedDataset) -> NormalizedLedgerInput:
    """Normalize all events/options while keeping records in their source currency."""
    events = tuple(normalize_event(event) for event in dataset.events)
    options = tuple(normalize_payment_option(option) for option in dataset.payment_options)
    return NormalizedLedgerInput(
        preferences_by_user=MappingProxyType(
            {
                profile.user_id: normalize_profile_preferences(profile)
                for profile in dataset.profiles
            }
        ),
        events=events,
        events_by_id=MappingProxyType({event.event_id: event for event in events}),
        events_by_user=_group_by(events, "user_id"),
        payment_options=options,
        payment_options_by_id=MappingProxyType(
            {option.payment_option_id: option for option in options}
        ),
        payment_options_by_request=_group_by(options, "request_id"),
    )


def reconstruct_effective_financial_state(
    *,
    normalized: NormalizedLedgerInput,
    converter: CurrencyConverter,
    user_id: str,
    request_date: date,
    message_facts: Sequence[MessageFact] = (),
) -> EffectiveFinancialState:
    """Build a conservative future cash ledger without selecting a payment plan.

    The profile balance is already the available balance at the decision point,
    so historical settled transactions are audit-only.  Future obligations and
    confirmed income remain explicitly dated for the later forecast step.
    """
    preferences = normalized.preferences_by_user[user_id]
    facts = resolve_message_conflicts(tuple(fact for fact in message_facts if fact.user_id == user_id))
    facts_by_event: dict[str, set[MessageFactType]] = {}
    for fact in facts:
        if fact.related_event_id:
            facts_by_event.setdefault(fact.related_event_id, set()).add(fact.fact_type)
    transfer_pair_ids: set[str] = set()
    if any(fact.fact_type is MessageFactType.INTERNAL_TRANSFER for fact in facts):
        user_events = normalized.events_by_user.get(user_id, ())
        for debit in user_events:
            if debit.direction is not Direction.DEBIT or debit.source_amount is None or "transfer" not in debit.description.casefold():
                continue
            for credit in user_events:
                if (
                    credit.direction is Direction.CREDIT
                    and credit.source_amount == debit.source_amount
                    and credit.currency is debit.currency
                    and credit.cash_date == debit.cash_date
                    and "transfer" in credit.description.casefold()
                ):
                    transfer_pair_ids.update((debit.event_id, credit.event_id))
    flows: list[EffectiveCashRecord] = []
    reserved: list[EffectiveCashRecord] = []
    audit: list[LedgerAuditEntry] = []
    for event in normalized.events_by_user.get(user_id, ()):
        if event.event_id in transfer_pair_ids:
            audit.append(LedgerAuditEntry(event.event_id, "excluded", "evidence-confirmed internal transfer pair"))
            continue
        types = facts_by_event.get(event.event_id, set())
        status = EventStatus.SETTLED if MessageFactType.EVENT_SETTLED in types else event.status
        retry = MessageFactType.FAILED_DEBIT_RETRY in types
        if event.direction is Direction.NON_CASH or event.event_type is EventType.INVESTMENT_VALUATION:
            audit.append(LedgerAuditEntry(event.event_id, "excluded", "non-cash or unrealized valuation"))
            continue
        if event.source_amount is None:
            audit.append(LedgerAuditEntry(event.event_id, "excluded", "amount unresolved; never treated as zero"))
            continue
        if status in (EventStatus.CANCELLED, EventStatus.UNREALIZED):
            audit.append(LedgerAuditEntry(event.event_id, "excluded", f"{status.value} event"))
            continue
        if status is EventStatus.FAILED and not retry:
            audit.append(LedgerAuditEntry(event.event_id, "excluded", "failed debit without confirmed retry"))
            continue
        if event.cash_date < request_date and not (status is EventStatus.PENDING or retry):
            audit.append(LedgerAuditEntry(event.event_id, "excluded", "settled before request date"))
            continue
        conversion = converter.convert_with_audit(
            source_id=event.event_id,
            amount=event.source_amount,
            from_currency=event.currency,
            to_currency=preferences.home_currency,
            rate_date=event.cash_date,
        )
        if conversion.status is ConversionStatus.MISSING_RATE:
            audit.append(LedgerAuditEntry(event.event_id, "excluded", "missing dated FX rate", conversion))
            continue
        amount = conversion.converted_amount
        assert amount is not None
        signed = -amount if event.direction is Direction.DEBIT else amount
        record = EffectiveCashRecord(event.event_id, event.cash_date, signed, "event", status.value)
        if event.direction is Direction.DEBIT and (status is EventStatus.PENDING or retry):
            reserved.append(record)
            audit.append(LedgerAuditEntry(event.event_id, "reserved", "pending debit or confirmed retry", conversion))
        elif event.direction is Direction.CREDIT and status is EventStatus.PENDING:
            audit.append(LedgerAuditEntry(event.event_id, "excluded", "pending credit is not available cash", conversion))
        elif event.direction is Direction.CREDIT and status is EventStatus.SCHEDULED and event.event_type is not EventType.INCOME:
            audit.append(LedgerAuditEntry(event.event_id, "excluded", "scheduled non-income credit is unconfirmed", conversion))
        else:
            flows.append(record)
            audit.append(LedgerAuditEntry(event.event_id, "included", "dated cash event", conversion))
    for fact in facts:
        if fact.fact_type is not MessageFactType.CONFIRMED_INCOME:
            continue
        if fact.amount is None or fact.currency is None or fact.effective_date is None:
            audit.append(LedgerAuditEntry(fact.message_id, "excluded", "confirmed-income message lacks amount, currency, or date"))
            continue
        if fact.effective_date < request_date:
            audit.append(LedgerAuditEntry(fact.message_id, "excluded", "confirmed-income date precedes request"))
            continue
        conversion = converter.convert_with_audit(
            source_id=fact.message_id,
            amount=fact.amount,
            from_currency=fact.currency,
            to_currency=preferences.home_currency,
            rate_date=fact.effective_date,
        )
        if conversion.status is ConversionStatus.MISSING_RATE:
            audit.append(LedgerAuditEntry(fact.message_id, "excluded", "missing dated FX rate", conversion))
            continue
        assert conversion.converted_amount is not None
        flows.append(EffectiveCashRecord(fact.message_id, fact.effective_date, conversion.converted_amount, "confirmed_income", "message-confirmed"))
        audit.append(LedgerAuditEntry(fact.message_id, "included", "message-confirmed income", conversion))
    return EffectiveFinancialState(
        user_id=user_id,
        as_of_date=request_date,
        opening_available_balance=preferences.current_available_balance,
        home_currency=preferences.home_currency,
        cash_flows=tuple(sorted(flows, key=lambda item: (item.cash_date, item.source_id))),
        reserved_obligations=tuple(sorted(reserved, key=lambda item: (item.cash_date, item.source_id))),
        audit=tuple(sorted(audit, key=lambda item: item.source_id)),
    )
