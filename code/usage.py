"""Offline-first model-usage accounting with no credential persistence."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

@dataclass(frozen=True, slots=True)
class UsageRecord:
    provider: str; model: str; calls: int; cache_hits: int; input_tokens: int; output_tokens: int; estimated_cost: Decimal

def render_usage_report(records: tuple[UsageRecord,...], request_count: int) -> str:
    calls=sum(r.calls for r in records); inp=sum(r.input_tokens for r in records); out=sum(r.output_tokens for r in records); cost=sum((r.estimated_cost for r in records),Decimal("0"))
    lines=["# Model Usage Report","","Final full-dataset run: deterministic offline mode.",f"Requests: {request_count}",f"Model calls: {calls}",f"Input tokens: {inp}",f"Output tokens: {out}",f"Total tokens: {inp+out}",f"Estimated total cost: {cost}",f"Average tokens per request: {(inp+out)/request_count if request_count else 0}",f"Average cost per request: {cost/request_count if request_count else 0}","","| Provider | Model | Calls | Cache hits | Input tokens | Output tokens | Estimated cost |","|---|---|---:|---:|---:|---:|---:|"]
    lines.extend(f"| {r.provider} | {r.model} | {r.calls} | {r.cache_hits} | {r.input_tokens} | {r.output_tokens} | {r.estimated_cost} |" for r in records)
    lines.append("\nNo API keys, credentials, or message/image source text are recorded.")
    return "\n".join(lines)+"\n"

def write_offline_usage_report(path: Path, request_count: int) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(render_usage_report((UsageRecord("offline","deterministic-rules",0,0,0,0,Decimal("0")),),request_count),encoding="utf-8")
