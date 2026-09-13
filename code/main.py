"""Terminal entry point for the Buy or Wait? solution.

Step 1 intentionally provides project configuration and command handling only.
It does not read financial data or generate predictions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import RunConfig, validate_dataset_dir
from currency import CurrencyConverter
from evidence import available_local_ocr, extract_message_facts, resolve_image_evidence, resolve_message_conflicts
from ingest import load_dataset
from ledger import normalize_ledger_input, reconstruct_effective_financial_state
from recurrence import build_forecast_rules
from relationships import build_relationship_graph


def build_parser() -> argparse.ArgumentParser:
    """Create the stable public command-line interface."""
    parser = argparse.ArgumentParser(
        description="Buy or Wait? financial decision agent (project scaffold)."
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("dataset"),
        help="Directory containing participant-facing inputs (default: dataset).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output.csv"),
        help="Destination for the completed output.csv (default: output.csv).",
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        default=Path(".audit"),
        help="Directory for non-submission audit artifacts (default: .audit).",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Opt in to optional LLM evidence extraction in later implementation steps.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Forbid network/model calls; this is the default execution posture.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate scaffold configuration without writing output or audit files.",
    )
    parser.add_argument(
        "--check-inputs",
        action="store_true",
        help="Load and validate every participant-facing CSV without writing files.",
    )
    parser.add_argument(
        "--audit-data",
        action="store_true",
        help="Build and print the aggregate relationship audit without writing files.",
    )
    parser.add_argument(
        "--extract-images",
        action="store_true",
        help="Resolve image-backed blank amounts with local OCR and report blocked evidence.",
    )
    parser.add_argument(
        "--extract-messages",
        action="store_true",
        help="Extract and summarize typed message facts without applying financial changes.",
    )
    parser.add_argument("--inspect-request", help="Inspect effective state for one evaluation request ID.")
    parser.add_argument("--show-rules", action="store_true", help="Include inferred recurrence rules in request inspection.")
    return parser


def parse_config(argv: list[str] | None = None) -> RunConfig:
    """Parse arguments and return a validated, side-effect-free configuration."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.use_llm and args.offline:
        parser.error("--use-llm and --offline cannot be used together")

    dataset_dir = args.dataset_dir.resolve()
    try:
        validate_dataset_dir(dataset_dir)
    except ValueError as error:
        parser.error(str(error))

    return RunConfig(
        dataset_dir=dataset_dir,
        output_path=args.output.resolve(),
        audit_dir=args.audit_dir.resolve(),
        use_llm=args.use_llm,
        offline=args.offline or not args.use_llm,
        dry_run=args.dry_run,
        check_inputs=args.check_inputs,
        audit_data=args.audit_data,
        extract_images=args.extract_images,
        extract_messages=args.extract_messages,
        inspect_request=args.inspect_request,
        show_rules=args.show_rules,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the Step 1 scaffold and return a stable process exit code."""
    config = parse_config(argv)
    if config.check_inputs or config.audit_data or config.extract_images or config.extract_messages or config.inspect_request:
        dataset = load_dataset(config.dataset_dir)
        if config.inspect_request:
            try:
                request = dataset.requests_by_id[config.inspect_request]
            except KeyError:
                print(f"unknown evaluation request_id: {config.inspect_request}", file=sys.stderr)
                return 2
            normalized = normalize_ledger_input(dataset)
            facts = resolve_message_conflicts(extract_message_facts(dataset.messages_by_user.get(request.user_id, ())))
            state = reconstruct_effective_financial_state(
                normalized=normalized,
                converter=CurrencyConverter(dataset.exchange_rates),
                user_id=request.user_id,
                request_date=request.request_date,
                message_facts=facts,
            )
            print(f"Effective state for {request.request_id}: {len(state.cash_flows)} cash flows, {len(state.reserved_obligations)} reserved obligations")
            if config.show_rules:
                rules = build_forecast_rules(
                    normalized=normalized,
                    converter=CurrencyConverter(dataset.exchange_rates),
                    user_id=request.user_id,
                    as_of_date=request.request_date,
                    message_facts=facts,
                )
                print(f"Conservative recurrence rules: {len(rules)}")
                for rule in rules:
                    print(f"  {rule.next_occurrence.isoformat()} {rule.direction.value} {rule.category}: {rule.amount}")
            return 0
        if config.extract_messages:
            facts = resolve_message_conflicts(extract_message_facts(dataset.messages))
            print(f"Message evidence extraction completed. Resolved facts: {len(facts)}")
            return 0
        if config.extract_images:
            report = resolve_image_evidence(
                dataset=dataset,
                normalized=normalize_ledger_input(dataset),
                image_directory=config.dataset_dir / "media" / "images",
                ocr_engine=available_local_ocr(),
            )
            print(report.render())
            return 0
        if config.audit_data:
            print(build_relationship_graph(dataset).audit.render())
            return 0
        print(dataset.report.render())
        return 0
    mode = "dry run" if config.dry_run else "scaffold check"
    print(f"Buy or Wait? {mode} succeeded.")
    print(f"Dataset directory: {config.dataset_dir}")
    print(f"Output path: {config.output_path}")
    print(f"Audit directory: {config.audit_dir}")
    print(f"LLM mode: {'enabled' if config.use_llm else 'offline'}")
    print("No financial data was read and no files were written by the Step 1 scaffold.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
