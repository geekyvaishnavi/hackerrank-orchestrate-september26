"""Capacity calculations independent from payment-method recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from domain import ScheduledPayment
from forecast import simulate_daily_balances
from ledger import EffectiveFinancialState
from recurrence import ForecastRule


@dataclass(frozen=True, slots=True)
class PaymentCapacity:
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: date | None


def calculate_payment_capacity(
    *,
    state: EffectiveFinancialState,
    minimum_balance_to_keep: Decimal,
    requested_amount: Decimal,
    rules: Sequence[ForecastRule] = (),
    horizon_days: int = 90,
) -> PaymentCapacity:
    """Find a simulator-verified safe amount today and first safe full-pay date."""
    if not isinstance(requested_amount, Decimal) or requested_amount < Decimal("0"):
        raise ValueError("requested_amount must be a non-negative Decimal")
    baseline = simulate_daily_balances(state=state, minimum_balance_to_keep=minimum_balance_to_keep, rules=rules, horizon_days=horizon_days)
    headroom = max(Decimal("0"), baseline.running_minimum - minimum_balance_to_keep)
    safe = min(requested_amount, headroom)
    if safe and not simulate_daily_balances(
        state=state, minimum_balance_to_keep=minimum_balance_to_keep, rules=rules,
        candidate_payments=(ScheduledPayment(state.as_of_date, safe),), horizon_days=horizon_days,
    ).feasible:
        safe = Decimal("0")
    earliest: date | None = None
    for offset in range(horizon_days + 1):
        payment_date = state.as_of_date + timedelta(days=offset)
        simulation = simulate_daily_balances(
            state=state, minimum_balance_to_keep=minimum_balance_to_keep, rules=rules,
            candidate_payments=(ScheduledPayment(payment_date, requested_amount),), horizon_days=horizon_days,
        )
        if simulation.feasible:
            earliest = payment_date
            break
    return PaymentCapacity(safe, earliest)
