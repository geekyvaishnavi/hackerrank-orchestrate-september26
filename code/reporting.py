"""Deterministic submission serialization and per-request audit records."""
from __future__ import annotations
import csv, json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from domain import format_money
from currency import CurrencyConverter
from decision import calculate_payment_capacity, select_decision
from evidence import extract_message_facts, resolve_message_conflicts
from explain import explain_decision
from ingest import OUTPUT_COLUMNS
from ledger import normalize_ledger_input, reconstruct_effective_financial_state
from plans import enumerate_plan_candidates
from recurrence import build_forecast_rules

@dataclass(frozen=True, slots=True)
class DatasetAudit:
    evaluation_request_count: int
    sample_request_count: int
    option_count_histogram: dict[int, int]
    request_message_count: int
    request_image_count: int
    event_message_count: int
    event_image_count: int
    user_only_message_count: int
    user_only_image_count: int
    def render(self) -> str:
        option_lines = tuple(f"{count} options: {requests} requests" for count, requests in sorted(self.option_count_histogram.items()))
        return "\n".join(("Dataset relationship audit:", f"Evaluation requests: {self.evaluation_request_count}", f"Sample requests: {self.sample_request_count}", *option_lines, f"Request-linked messages: {self.request_message_count}", f"Request-linked images: {self.request_image_count}", f"Event-linked messages: {self.event_message_count}", f"Event-linked images: {self.event_image_count}", f"User-only messages: {self.user_only_message_count}", f"User-only images: {self.user_only_image_count}"))

def _plan(decision) -> str:
    if decision.candidate is None: return "none"
    return "|".join(f"{p.payment_date.isoformat()}:{format_money(p.amount)}" for p in decision.candidate.payments)

def generate_output(dataset, output_path: Path, audit_dir: Path) -> None:  # type: ignore[no-untyped-def]
    normalized = normalize_ledger_input(dataset); converter = CurrencyConverter(dataset.exchange_rates)
    audit_dir.mkdir(parents=True, exist_ok=True)
    records=[]; audits=[]
    for request in dataset.requests:
        preferences=normalized.preferences_by_user[request.user_id]
        facts=resolve_message_conflicts(extract_message_facts(dataset.messages_by_user.get(request.user_id, ())))
        state=reconstruct_effective_financial_state(normalized=normalized, converter=converter, user_id=request.user_id, request_date=request.request_date, message_facts=facts)
        rules=build_forecast_rules(normalized=normalized, converter=converter, user_id=request.user_id, as_of_date=request.request_date, message_facts=facts)
        capacity=calculate_payment_capacity(state=state, minimum_balance_to_keep=preferences.minimum_balance_to_keep, requested_amount=request.requested_amount, rules=rules)
        assessments=enumerate_plan_candidates(request=request, normalized=normalized, state=state, capacity=capacity, rules=rules)
        decision=select_decision(request=request, capacity=capacity, assessments=assessments)
        explanation=explain_decision(decision=decision, state=state, minimum_balance_to_keep=preferences.minimum_balance_to_keep)
        records.append({"request_id":request.request_id,"amount_safe_to_pay":format_money(decision.amount_safe_to_pay),"affordability_status":decision.affordability_status.value,"recommended_payment_method":decision.recommended_payment_method.value,"payment_plan":_plan(decision),"earliest_date_for_full_payment":decision.earliest_date_for_full_payment.isoformat() if decision.earliest_date_for_full_payment else "","spending_changes_needed":"|".join(decision.candidate.spending_changes) if decision.candidate and decision.candidate.spending_changes else "none","decision_explanation":explanation})
        audits.append({"request_id":request.request_id,"event_ids":[e.event_id for e in normalized.events_by_user[request.user_id]],"message_ids":[f.message_id for f in facts],"forecast_rules":[r.supporting_event_ids for r in rules],"candidate_count":len(assessments),"selected":decision.recommended_payment_method.value})
    with output_path.open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=OUTPUT_COLUMNS); writer.writeheader(); writer.writerows(records)
    with (audit_dir/"decisions.jsonl").open("w",encoding="utf-8") as f:
        for item in audits: f.write(json.dumps(item,sort_keys=True)+"\n")
