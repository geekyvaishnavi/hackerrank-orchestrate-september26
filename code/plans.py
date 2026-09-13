"""Enumerate payment candidates from supplied options and the strict partial rule."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from decision import PaymentCapacity
from domain import CandidatePlan, PaymentMethod, Request, ScheduledPayment
from forecast import ForecastResult, simulate_daily_balances
from ledger import EffectiveFinancialState, NormalizedLedgerInput
from recurrence import ForecastRule


@dataclass(frozen=True, slots=True)
class PlanAssessment:
    label: str
    candidate: CandidatePlan | None
    simulation: ForecastResult | None
    eligible: bool
    reason: str | None


def _simulate(candidate: CandidatePlan, state: EffectiveFinancialState, minimum: Decimal, rules: Sequence[ForecastRule]) -> ForecastResult:
    return simulate_daily_balances(state=state, minimum_balance_to_keep=minimum, rules=rules, candidate_payments=candidate.payments)


def _assessment(label: str, candidate: CandidatePlan, state: EffectiveFinancialState, minimum: Decimal, rules: Sequence[ForecastRule]) -> PlanAssessment:
    result = _simulate(candidate, state, minimum, rules)
    return PlanAssessment(label, candidate, result, result.feasible, None if result.feasible else f"forecast breaches on {result.first_breach_date.isoformat()}")


def _option_payments(option) -> tuple[ScheduledPayment, ...] | None:  # type: ignore[no-untyped-def]
    if option.payment_method is PaymentMethod.FULL_PAYMENT:
        if option.schedule.number_of_payments != 1 or option.schedule.payment_frequency_days is not None:
            return None
        return (ScheduledPayment(option.schedule.first_payment_date, option.payment_amount),)
    if option.payment_method is not PaymentMethod.INSTALLMENTS or option.schedule.payment_frequency_days is None:
        return None
    return tuple(
        ScheduledPayment(option.schedule.first_payment_date + timedelta(days=option.schedule.payment_frequency_days * index), option.payment_amount)
        for index in range(option.schedule.number_of_payments)
    )


def enumerate_plan_candidates(
    *,
    request: Request,
    normalized: NormalizedLedgerInput,
    state: EffectiveFinancialState,
    capacity: PaymentCapacity,
    rules: Sequence[ForecastRule] = (),
) -> tuple[PlanAssessment, ...]:
    """Return every allowed source-exact candidate and every rejection reason."""
    preferences = normalized.preferences_by_user[request.user_id]
    assessments: list[PlanAssessment] = []
    for option in normalized.payment_options_by_request.get(request.request_id, ()):
        label = option.payment_option_id
        if option.payment_method not in (PaymentMethod.FULL_PAYMENT, PaymentMethod.INSTALLMENTS):
            assessments.append(PlanAssessment(label, None, None, False, "unsupported provider method"))
            continue
        if option.payment_method not in preferences.payment_methods:
            assessments.append(PlanAssessment(label, None, None, False, "payment method not accepted by profile"))
            continue
        if option.payment_method is PaymentMethod.INSTALLMENTS and (
            preferences.max_installment_months is None or option.schedule.number_of_payments > preferences.max_installment_months
        ):
            assessments.append(PlanAssessment(label, None, None, False, "installment term exceeds profile maximum"))
            continue
        payments = _option_payments(option)
        if payments is None or sum(payment.amount for payment in payments) != option.total_payable_amount:
            assessments.append(PlanAssessment(label, None, None, False, "provider option has an invalid payment schedule"))
            continue
        if option.total_payable_amount != request.requested_amount + option.financing_fee:
            assessments.append(PlanAssessment(label, None, None, False, "provider total does not equal request plus fee"))
            continue
        candidate = CandidatePlan(option.payment_method, payments, option.total_payable_amount, option.payment_option_id)
        assessments.append(_assessment(label, candidate, state, preferences.minimum_balance_to_keep, rules))
    safe = capacity.amount_safe_to_pay
    full_date = capacity.earliest_date_for_full_payment
    if (
        request.allows_partial_payment
        and PaymentMethod.PARTIAL_PAYMENT in preferences.payment_methods
        and Decimal("0") < safe < request.requested_amount
        and full_date is not None
        and full_date <= request.desired_completion_date
    ):
        candidate = CandidatePlan(PaymentMethod.PARTIAL_PAYMENT, (ScheduledPayment(request.request_date, safe), ScheduledPayment(full_date, request.requested_amount - safe)), request.requested_amount, None)
        assessments.append(_assessment("partial_payment", candidate, state, preferences.minimum_balance_to_keep, rules))
    else:
        assessments.append(PlanAssessment("partial_payment", None, None, False, "partial-payment eligibility requirements not met"))
    if (
        full_date is not None
        and full_date > request.request_date
        and full_date <= request.desired_completion_date
        and PaymentMethod.FULL_PAYMENT in preferences.payment_methods
    ):
        candidate = CandidatePlan(PaymentMethod.WAIT, (ScheduledPayment(full_date, request.requested_amount),), request.requested_amount, None)
        assessments.append(_assessment("wait", candidate, state, preferences.minimum_balance_to_keep, rules))
    else:
        assessments.append(PlanAssessment("wait", None, None, False, "wait eligibility requirements not met"))
    return tuple(assessments)
