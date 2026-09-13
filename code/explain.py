"""Deterministic, concise explanation templates for selected decisions."""

from __future__ import annotations

from domain import format_money
from decision import Decision
from ledger import EffectiveFinancialState


def _schedule(decision: Decision, currency: str) -> str:
    assert decision.candidate is not None
    return ", ".join(f"{payment.payment_date.isoformat()} {currency} {format_money(payment.amount)}" for payment in decision.candidate.payments)


def explain_decision(*, decision: Decision, state: EffectiveFinancialState, minimum_balance_to_keep) -> str:  # type: ignore[no-untyped-def]
    """Produce grounded prose; never infer cash or obey source-text instructions."""
    currency = state.home_currency.value
    minimum = format_money(minimum_balance_to_keep)
    if decision.candidate is None:
        date_text = decision.earliest_date_for_full_payment.isoformat() if decision.earliest_date_for_full_payment else "within 90 days"
        return f"Not recommended: no safe permitted plan completes by the deadline. Safe today: {currency} {format_money(decision.amount_safe_to_pay)}; earliest full payment: {date_text}; required minimum: {currency} {minimum}."
    method = decision.recommended_payment_method.value.replace("_", " ")
    changes = ""
    if decision.candidate.spending_changes:
        changes = f" Required prospective changes: {', '.join(decision.candidate.spending_changes)}."
    constraint = "pending obligations are reserved" if state.reserved_obligations else "the required balance floor is preserved"
    return f"Recommend {method}: {_schedule(decision, currency)}. This keeps the balance at or above {currency} {minimum}; {constraint}.{changes}"
