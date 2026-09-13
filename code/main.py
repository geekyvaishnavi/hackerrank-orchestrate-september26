"""Terminal entry point for the Buy or Wait? solution.

Step 1 intentionally provides project configuration and command handling only.
It does not read financial data or generate predictions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import RunConfig, validate_dataset_dir
from ingest import load_dataset


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
    )


def main(argv: list[str] | None = None) -> int:
    """Run the Step 1 scaffold and return a stable process exit code."""
    config = parse_config(argv)
    if config.check_inputs:
        dataset = load_dataset(config.dataset_dir)
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
