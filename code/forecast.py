"""Pure, conservative daily balance simulation for the mandatory 90-day horizon."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from domain import Direction, ScheduledPayment
from ledger import EffectiveFinancialState
from recurrence import ForecastRule


@dataclass(frozen=True, slots=True)
class ForecastCashItem:
    source_id: str
    cash_date: date
    amount: Decimal
    kind: str


@dataclass(frozen=True, slots=True)
class DailyBalance:
    cash_date: date
    opening_balance: Decimal
    items: tuple[ForecastCashItem, ...]
    closing_balance: Decimal
    lowest_balance: Decimal


@dataclass(frozen=True, slots=True)
class ForecastResult:
    days: tuple[DailyBalance, ...]
    running_minimum: Decimal
    first_breach_date: date | None
    first_breach_source_id: str | None
    minimum_balance_to_keep: Decimal

    @property
    def feasible(self) -> bool:
        return self.first_breach_date is None


def _rule_items(rules: Sequence[ForecastRule], start: date, end: date) -> list[ForecastCashItem]:
    items: list[ForecastCashItem] = []
    for index, rule in enumerate(rules):
        occurrence = rule.next_occurrence
        while occurrence <= end:
            if occurrence >= start:
                amount = -rule.amount if rule.direction is Direction.DEBIT else rule.amount
                source = (rule.supporting_message_ids or rule.supporting_event_ids or (f"rule_{index}",))[0]
                items.append(ForecastCashItem(f"rule:{source}:{occurrence.isoformat()}", occurrence, amount, "recurrence"))
            if rule.cadence_days is None:
                break
            occurrence += timedelta(days=rule.cadence_days)
    return items


def simulate_daily_balances(
    *,
    state: EffectiveFinancialState,
    minimum_balance_to_keep: Decimal,
    rules: Sequence[ForecastRule] = (),
    candidate_payments: Sequence[ScheduledPayment] = (),
    horizon_days: int = 90,
) -> ForecastResult:
    """Simulate inclusively from the request date through day 90 (or later payment).

    Debits are applied before credits on each date. This makes a salary on the
    same day as an obligation unavailable until that obligation is reserved.
    """
    if not isinstance(minimum_balance_to_keep, Decimal):
        raise TypeError("minimum_balance_to_keep must be a Decimal")
    if horizon_days < 0:
        raise ValueError("horizon_days must not be negative")
    if not all(isinstance(payment, ScheduledPayment) for payment in candidate_payments):
        raise TypeError("candidate_payments must contain ScheduledPayment values")
    end = state.as_of_date + timedelta(days=horizon_days)
    if candidate_payments:
        end = max(end, max(payment.payment_date for payment in candidate_payments))
    items = [
        ForecastCashItem(record.source_id, record.cash_date, record.amount, record.kind)
        for record in state.cash_flows + state.reserved_obligations
        if state.as_of_date <= record.cash_date <= end
    ]
    known_sources = {item.source_id for item in items}
    for item in _rule_items(rules, state.as_of_date, end):
        # Confirmed one-off income can occur in both the state and rules; only
        # one source fact may affect the balance.
        source_key = item.source_id.split(":", 2)[1]
        if source_key not in known_sources:
            items.append(item)
    for number, payment in enumerate(candidate_payments, start=1):
        items.append(ForecastCashItem(f"candidate_payment_{number}", payment.payment_date, -payment.amount, "candidate_payment"))
    by_date: dict[date, list[ForecastCashItem]] = {}
    for item in items:
        by_date.setdefault(item.cash_date, []).append(item)
    balance = state.opening_available_balance
    running_minimum = balance
    breach_date: date | None = None
    breach_source: str | None = None
    days: list[DailyBalance] = []
    current = state.as_of_date
    while current <= end:
        opening = balance
        day_items = tuple(sorted(by_date.get(current, ()), key=lambda item: (item.amount >= Decimal("0"), item.source_id)))
        lowest = balance
        for item in day_items:
            balance += item.amount
            lowest = min(lowest, balance)
            running_minimum = min(running_minimum, balance)
            if breach_date is None and balance < minimum_balance_to_keep:
                breach_date, breach_source = current, item.source_id
        days.append(DailyBalance(current, opening, day_items, balance, lowest))
        current += timedelta(days=1)
    return ForecastResult(tuple(days), running_minimum, breach_date, breach_source, minimum_balance_to_keep)
