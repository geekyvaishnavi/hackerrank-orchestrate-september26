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
