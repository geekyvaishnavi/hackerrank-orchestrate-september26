"""Step 20 tests for transparent offline usage reporting."""
from __future__ import annotations
import sys, tempfile, unittest
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"code"))
from usage import UsageRecord, render_usage_report, write_offline_usage_report

class UsageTests(unittest.TestCase):
    def test_aggregates_tokens_cost_and_never_contains_secrets(self):
        text=render_usage_report((UsageRecord("provider","model",2,1,10,5,Decimal("0.02")),),5)
        self.assertIn("Model calls: 2",text); self.assertIn("Total tokens: 15",text)
        self.assertNotIn("sk-",text); self.assertNotIn("API_KEY",text)
    def test_offline_report_is_zero_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"usage_report.md"; write_offline_usage_report(path,250)
            self.assertIn("Model calls: 0",path.read_text())
