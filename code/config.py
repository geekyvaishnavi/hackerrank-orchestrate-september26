"""Configuration primitives shared by the command-line entry point."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunConfig:
    """Validated paths and execution switches for a single run."""

    dataset_dir: Path
    output_path: Path
    audit_dir: Path
    use_llm: bool
    offline: bool
    dry_run: bool
    check_inputs: bool
    audit_data: bool


def validate_dataset_dir(dataset_dir: Path) -> None:
    """Confirm only that the configured input location exists and is a directory.

    CSV-schema validation belongs to the planned ingestion step, not this scaffold.
    """
    if not dataset_dir.exists():
        raise ValueError(f"dataset directory does not exist: {dataset_dir}")
    if not dataset_dir.is_dir():
        raise ValueError(f"dataset path is not a directory: {dataset_dir}")
