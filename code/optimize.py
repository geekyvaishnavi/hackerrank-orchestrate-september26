"""Bounded, permission-aware prospective spending-change search."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import combinations, product
from typing import Sequence

from domain import CandidatePlan, Direction, Flexibility
from forecast import ForecastResult, simulate_daily_balances
from ledger import EffectiveFinancialState, NormalizedLedgerInput
from recurrence import ForecastRule


@dataclass(frozen=True, slots=True)
class SpendingChangeResult:
    actions: tuple[str, ...]
    candidate: CandidatePlan | None
    simulation: ForecastResult | None
    reason: str


def _eligible_actions(normalized: NormalizedLedgerInput, user_id: str, rules: Sequence[ForecastRule]) -> tuple[tuple[str, ForecastRule], ...]:
    preferences = normalized.preferences_by_user[user_id]
    actions: list[tuple[str, ForecastRule]] = []
    for rule in rules:
        if rule.direction is not Direction.DEBIT or not rule.supporting_event_ids:
            continue
        event_id = rule.supporting_event_ids[-1]
        event = normalized.events_by_id[event_id]
        if rule.category in preferences.protected_categories or event.currency is not preferences.home_currency:
            continue
        if event.flexibility in (Flexibility.STOPPABLE, Flexibility.REDUCIBLE_OR_STOPPABLE) and rule.category in preferences.stoppable_categories:
            actions.append((f"stop:{event_id}", rule))
        if (
            event.flexibility in (Flexibility.REDUCIBLE, Flexibility.REDUCIBLE_OR_STOPPABLE)
            and rule.category in preferences.reducible_categories
            and event.minimum_allowed_source_amount is not None
            and event.minimum_allowed_source_amount < rule.amount
        ):
            # The legal floor is always considered. Additional exact amounts are
            # derived from the required deficit by later decision logic; this
            # step deliberately enumerates only the lowest permitted baseline.
            actions.append((f"reduce_to:{event_id}:{event.minimum_allowed_source_amount}", rule))
    return tuple(actions)


def _apply(actions: Sequence[tuple[str, ForecastRule]], rules: Sequence[ForecastRule]) -> tuple[ForecastRule, ...]:
    replacements = {rule.supporting_event_ids[-1]: (action, rule) for action, rule in actions}
    changed: list[ForecastRule] = []
    for rule in rules:
        match = replacements.get(rule.supporting_event_ids[-1]) if rule.supporting_event_ids else None
        if match is None:
            changed.append(rule)
            continue
        action, _ = match
        if action.startswith("stop:"):
            continue
        changed.append(replace(rule, amount=Decimal(action.rsplit(":", 1)[1])))
    return tuple(changed)


def optimize_spending_changes(
    *,
    base_candidate: CandidatePlan,
    state: EffectiveFinancialState,
    normalized: NormalizedLedgerInput,
    rules: Sequence[ForecastRule],
) -> SpendingChangeResult:
    """Return the minimal independently feasible set of at most three actions."""
    preferences = normalized.preferences_by_user[state.user_id]
    baseline = simulate_daily_balances(state=state, minimum_balance_to_keep=preferences.minimum_balance_to_keep, rules=rules, candidate_payments=base_candidate.payments)
    if baseline.feasible:
        return SpendingChangeResult((), base_candidate, baseline, "no changes necessary")
    choices_by_event: dict[str, list[tuple[str, ForecastRule]]] = {}
    for action, rule in _eligible_actions(normalized, state.user_id, rules):
        choices_by_event.setdefault(rule.supporting_event_ids[-1], []).append((action, rule))
    event_ids = tuple(sorted(choices_by_event))
    feasible: list[SpendingChangeResult] = []
    for count in range(1, min(3, len(event_ids)) + 1):
        for selected_ids in combinations(event_ids, count):
            for selected in product(*(choices_by_event[event_id] for event_id in selected_ids)):
                changed_rules = _apply(selected, rules)
                simulation = simulate_daily_balances(state=state, minimum_balance_to_keep=preferences.minimum_balance_to_keep, rules=changed_rules, candidate_payments=base_candidate.payments)
                if simulation.feasible:
                    actions = tuple(sorted(action for action, _ in selected))
                    candidate = replace(base_candidate, spending_changes=actions)
                    feasible.append(SpendingChangeResult(actions, candidate, simulation, "feasible with permitted prospective changes"))
        if feasible:
            break
    if not feasible:
        return SpendingChangeResult((), None, baseline, "no permitted change set makes the candidate safe")
    rule_by_action = {
        action: rule for choices in choices_by_event.values() for action, rule in choices
    }
    def rank(result: SpendingChangeResult) -> tuple:
        total_reduction = sum(
            rule_by_action[action].amount if action.startswith("stop:") else rule_by_action[action].amount - Decimal(action.rsplit(":", 1)[1])
            for action in result.actions
        )
        return (len(result.actions), total_reduction, result.actions)
    return min(feasible, key=rank)
