"""Focused Step 1 tests for the public command-line scaffold."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "code" / "main.py"


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class CliTests(unittest.TestCase):
    def test_help_is_available(self) -> None:
        result = run_cli("--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("--dataset-dir", result.stdout)
        self.assertIn("--dry-run", result.stdout)


    def test_missing_dataset_has_a_stable_configuration_error(self) -> None:
        result = run_cli("--dataset-dir", "missing-dataset")

        self.assertEqual(result.returncode, 2)
        self.assertIn("dataset directory does not exist", result.stderr)


    def test_dry_run_does_not_write_the_requested_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_directory = Path(temporary_directory)
            output = temporary_directory / "output.csv"
            audit_dir = temporary_directory / "audit"

            result = run_cli(
                "--dataset-dir",
                "dataset",
                "--output",
                str(output),
                "--audit-dir",
                str(audit_dir),
                "--dry-run",
            )

            self.assertEqual(result.returncode, 0)
            self.assertIn("dry run succeeded", result.stdout)
            self.assertFalse(output.exists())
            self.assertFalse(audit_dir.exists())
