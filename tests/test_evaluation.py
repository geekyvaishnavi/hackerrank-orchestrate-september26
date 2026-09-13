"""Step 19 integration, invariant, and reproducibility smoke tests."""
from __future__ import annotations
import csv, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"code"))
from ingest import OUTPUT_COLUMNS, load_dataset
from reporting import generate_output
from validate import validate_output

class EvaluationTests(unittest.TestCase):
    def test_full_output_is_complete_valid_and_byte_stable(self) -> None:
        root=Path(__file__).resolve().parents[1]; dataset=load_dataset(root/"dataset")
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); first=base/"first.csv"; second=base/"second.csv"
            generate_output(dataset,first,base/"audit1"); generate_output(dataset,second,base/"audit2")
            validate_output(first,dataset); validate_output(second,dataset)
            self.assertEqual(first.read_bytes(),second.read_bytes())
            self.assertEqual((base/"audit1"/"decisions.jsonl").read_bytes(),(base/"audit2"/"decisions.jsonl").read_bytes())
            with first.open(newline="",encoding="utf-8") as handle:
                rows=list(csv.DictReader(handle)); self.assertEqual(tuple(rows[0]),OUTPUT_COLUMNS)
            self.assertEqual(len(rows),250)
