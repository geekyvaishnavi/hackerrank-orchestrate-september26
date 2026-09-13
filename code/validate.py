"""Independent structural validation for generated submissions."""
from __future__ import annotations
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from domain import AffordabilityStatus, PaymentMethod
from ingest import OUTPUT_COLUMNS

class OutputValidationError(ValueError): pass

def validate_output(path: Path, dataset) -> None:  # type: ignore[no-untyped-def]
    with path.open(encoding="utf-8",newline="") as f:
        reader=csv.DictReader(f); rows=list(reader)
        if tuple(reader.fieldnames or ()) != OUTPUT_COLUMNS: raise OutputValidationError("header/order mismatch")
    expected=[r.request_id for r in dataset.requests]
    if [r["request_id"] for r in rows] != expected: raise OutputValidationError("request_id coverage/order mismatch")
    for n,row in enumerate(rows,2):
        try: amount=Decimal(row["amount_safe_to_pay"])
        except InvalidOperation as e: raise OutputValidationError(f"row {n} amount_safe_to_pay invalid") from e
        request=dataset.requests_by_id[row["request_id"]]
        if not Decimal("0") <= amount <= request.requested_amount: raise OutputValidationError(f"row {n} amount_safe_to_pay out of bounds")
        if row["affordability_status"] not in {x.value for x in AffordabilityStatus}: raise OutputValidationError(f"row {n} affordability_status invalid")
        if row["recommended_payment_method"] not in {x.value for x in PaymentMethod}: raise OutputValidationError(f"row {n} recommended_payment_method invalid")
        if row["payment_plan"] != "none":
            payments=[]
            for piece in row["payment_plan"].split("|"):
                d,a=piece.split(":",1); payments.append((date.fromisoformat(d),Decimal(a)))
            if payments != sorted(payments): raise OutputValidationError(f"row {n} payment plan not chronological")
            if sum(a for _,a in payments) <= 0: raise OutputValidationError(f"row {n} payment plan amount invalid")
        if row["affordability_status"] == "affordable_now" and row["earliest_date_for_full_payment"] != request.request_date.isoformat(): raise OutputValidationError(f"row {n} affordable_now date invalid")
        if row["spending_changes_needed"] != "none" and len(row["spending_changes_needed"].split("|")) > 3: raise OutputValidationError(f"row {n} too many spending changes")
        if not row["decision_explanation"]: raise OutputValidationError(f"row {n} explanation blank")
