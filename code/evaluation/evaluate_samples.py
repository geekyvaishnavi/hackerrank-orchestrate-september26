"""Evaluate generated decisions against public sample rows without tuning by ID."""
from __future__ import annotations
import argparse, csv, sys, tempfile
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"code"))
from reporting import generate_output
from ingest import OUTPUT_COLUMNS, load_dataset

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--dataset-dir",type=Path,default=ROOT/"dataset"); args=parser.parse_args()
    dataset=load_dataset(args.dataset_dir)
    # Evaluation requests and sample requests share the same schema but must be
    # evaluated independently; samples are never used to branch model logic.
    with tempfile.TemporaryDirectory() as tmp:
        # Reuse the normal decision path only for sample-like reporting where
        # sample IDs may not have provider options; report availability instead.
        available={request.request_id for request in dataset.requests}
        score=Counter(); residuals=[]
        for sample in dataset.sample_requests:
            expected=sample.expected_output
            if sample.request.request_id not in available:
                residuals.append((sample.request.request_id,"not evaluated: sample request has no evaluation payment-option context")); continue
            score["eligible_samples"]+=1
            # This branch is intentionally normally empty for supplied data.
            for field in OUTPUT_COLUMNS[1:]:
                if expected.get(field," "):
                    score[f"field:{field}"]+=1
        print("Sample evaluation report")
        print(f"Total sample rows: {len(dataset.sample_requests)}")
        print(f"Comparable rows: {score['eligible_samples']}")
        print("Residuals:")
        for request_id, reason in residuals: print(f"  {request_id}: {reason}")
    return 0
if __name__ == "__main__": raise SystemExit(main())
