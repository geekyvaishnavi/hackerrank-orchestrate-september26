"""Conservative recurrence inference from effective event history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Sequence

from currency import ConversionStatus, CurrencyConverter
from evidence import MessageFact, MessageFactType, resolve_message_conflicts
from ledger import NormalizedLedgerInput
from domain import Direction, EventStatus, EventType


@dataclass(frozen=True, slots=True)
class ForecastRule:
    user_id: str
    category: str
    direction: Direction
    cadence_days: int | None
    next_occurrence: date
    amount: Decimal
    supporting_event_ids: tuple[str, ...]
    supporting_message_ids: tuple[str, ...]
    confidence: Decimal
    amount_selection: str
    detail: str


def _cadence(intervals: Sequence[int]) -> int | None:
    if len(intervals) < 2:
        return None
    middle = int(median(intervals))
    if 5 <= middle <= 9 and max(intervals) - min(intervals) <= 2:
        return 7
    if 27 <= middle <= 32 and max(intervals) - min(intervals) <= 5:
        return 30
    if 80 <= middle <= 100 and max(intervals) - min(intervals) <= 12:
        return 91
    return None


def build_forecast_rules(
    *,
    normalized: NormalizedLedgerInput,
    converter: CurrencyConverter,
    user_id: str,
    as_of_date: date,
    message_facts: Sequence[MessageFact] = (),
) -> tuple[ForecastRule, ...]:
    """Infer only three-or-more-instance cadence rules and dated confirmed income."""
    preferences = normalized.preferences_by_user[user_id]
    facts = resolve_message_conflicts(tuple(fact for fact in message_facts if fact.user_id == user_id))
    ended_income = any(fact.fact_type is MessageFactType.INCOME_ENDED for fact in facts)
    salary_facts = [fact for fact in facts if fact.fact_type is MessageFactType.SALARY_AMOUNT and fact.amount and fact.currency]
    rent_facts = [fact for fact in facts if fact.fact_type is MessageFactType.RENT_INCREASE and fact.percentage is not None]
    groups: dict[tuple[str, Direction, str], list] = {}
    for event in normalized.events_by_user.get(user_id, ()):
        if event.cash_date > as_of_date or event.status is not EventStatus.SETTLED:
            continue
        if event.direction is Direction.NON_CASH or event.source_amount is None or event.event_type is EventType.INVESTMENT_VALUATION:
            continue
        if "transfer" in event.description.casefold():
            continue
        key = (event.category, event.direction, event.description.casefold())
        groups.setdefault(key, []).append(event)
    rules: list[ForecastRule] = []
    for (category, direction, description), events in groups.items():
        events.sort(key=lambda event: (event.cash_date, event.event_id))
        if len(events) < 3:
            continue
        recent = events[-3:]
        cadence = _cadence([(right.cash_date - left.cash_date).days for left, right in zip(recent, recent[1:])])
        if cadence is None:
            continue
        if direction is Direction.CREDIT and category == "salary" and ended_income:
            continue
        converted: list[Decimal] = []
        for event in recent:
            audit = converter.convert_with_audit(source_id=event.event_id, amount=event.source_amount, from_currency=event.currency, to_currency=preferences.home_currency, rate_date=event.cash_date)
            if audit.status is ConversionStatus.MISSING_RATE or audit.converted_amount is None:
                converted = []
                break
            converted.append(audit.converted_amount)
        if not converted:
            continue
        amount = converted[-1]
        selection = "latest fixed commitment"
        message_ids: tuple[str, ...] = ()
        if direction is Direction.DEBIT and category in preferences.protected_categories:
            amount = max(converted)
            selection = "maximum of latest three protected essential amounts"
        if direction is Direction.CREDIT and category == "salary" and salary_facts:
            amendment = max(salary_facts, key=lambda fact: (fact.effective_date or as_of_date, fact.sent_at, fact.message_id))
            audit = converter.convert_with_audit(source_id=amendment.message_id, amount=amendment.amount, from_currency=amendment.currency, to_currency=preferences.home_currency, rate_date=amendment.effective_date or as_of_date)
            if audit.converted_amount is not None:
                amount = audit.converted_amount
                selection = "latest salary amendment"
                message_ids = (amendment.message_id,)
        if direction is Direction.DEBIT and category == "rent" and rent_facts:
            amendment = max(rent_facts, key=lambda fact: (fact.effective_date or as_of_date, fact.sent_at, fact.message_id))
            amount = amount * (Decimal("1") + amendment.percentage)
            selection = "latest rent-increase amendment"
            message_ids = (amendment.message_id,)
        rules.append(ForecastRule(user_id, category, direction, cadence, events[-1].cash_date + timedelta(days=cadence), amount, tuple(event.event_id for event in recent), message_ids, Decimal("1"), selection, description))
    for fact in facts:
        if fact.fact_type is MessageFactType.CONFIRMED_INCOME and fact.amount and fact.currency and fact.effective_date and fact.effective_date >= as_of_date:
            audit = converter.convert_with_audit(source_id=fact.message_id, amount=fact.amount, from_currency=fact.currency, to_currency=preferences.home_currency, rate_date=fact.effective_date)
            if audit.converted_amount is not None:
                rules.append(ForecastRule(user_id, "confirmed_income", Direction.CREDIT, None, fact.effective_date, audit.converted_amount, (), (fact.message_id,), Decimal("1"), "message-confirmed one-off income", "no recurrence inferred"))
    return tuple(sorted(rules, key=lambda rule: (rule.next_occurrence, rule.category, rule.detail)))
