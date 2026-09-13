"""Capacity calculations independent from payment-method recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from domain import AffordabilityStatus, CandidatePlan, PaymentMethod, Request, ScheduledPayment
from forecast import simulate_daily_balances
from ledger import EffectiveFinancialState
from recurrence import ForecastRule


@dataclass(frozen=True, slots=True)
class PaymentCapacity:
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: date | None


@dataclass(frozen=True, slots=True)
class Decision:
    """Selected safe candidate or an explicit no-recommendation result."""

    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod
    candidate: CandidatePlan | None
    amount_safe_to_pay: Decimal
    earliest_date_for_full_payment: date | None
    reason: str


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


def select_decision(
    *,
    request: Request,
    capacity: PaymentCapacity,
    assessments: Sequence,
    optimized_candidates: Sequence[CandidatePlan] = (),
) -> Decision:
    """Select one feasible candidate using the published deterministic ranking."""
    candidates = [item.candidate for item in assessments if item.eligible and item.candidate is not None]
    candidates.extend(optimized_candidates)
    by_deadline = [candidate for candidate in candidates if candidate.payments[-1].payment_date <= request.desired_completion_date]
    if not by_deadline:
        reasons = sorted({item.reason for item in assessments if item.reason})
        return Decision(
            AffordabilityStatus.NOT_AFFORDABLE,
            PaymentMethod.NOT_RECOMMENDED,
            None,
            capacity.amount_safe_to_pay,
            capacity.earliest_date_for_full_payment,
            reasons[0] if reasons else "no safe candidate completes by the deadline",
        )

    def option_number(candidate: CandidatePlan) -> int:
        if candidate.payment_option_id is None:
            return 10**12
        digits = "".join(character for character in candidate.payment_option_id if character.isdigit())
        return int(digits) if digits else 10**12

    selected = min(
        by_deadline,
        key=lambda candidate: (
            bool(candidate.spending_changes),
            candidate.total_payable_amount,
            candidate.payments[0].payment_date,
            len(candidate.payments),
            option_number(candidate),
        ),
    )
    if selected.method is PaymentMethod.WAIT:
        status = AffordabilityStatus.AFFORDABLE_LATER
    elif selected.method is PaymentMethod.FULL_PAYMENT and selected.payments[0].payment_date == request.request_date:
        status = AffordabilityStatus.AFFORDABLE_NOW
    else:
        status = AffordabilityStatus.AFFORDABLE_WITH_PLAN
    return Decision(status, selected.method, selected, capacity.amount_safe_to_pay, capacity.earliest_date_for_full_payment, "lowest-ranked safe candidate")
