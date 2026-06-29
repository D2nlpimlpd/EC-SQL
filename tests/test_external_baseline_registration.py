from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.aggregate_experiment_results import resolve_inputs, summary_rows_for_artifact
from scripts.register_external_baseline_results import register_csv, write_template


class ExternalBaselineRegistrationTests(unittest.TestCase):
    def test_bulk_import_writes_spider2_prefixed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "external.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "suite",
                        "system",
                        "model",
                        "cases",
                        "er",
                        "re",
                        "ser",
                        "baseline_reference",
                        "report_label",
                        "source_repo",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "suite": "spider2-sqlite",
                        "system": "chess_official",
                        "model": "gpt-4.1",
                        "cases": "24",
                        "er": "90",
                        "re": "80",
                        "ser": "75",
                        "baseline_reference": "CHESS official",
                        "report_label": "CHESS official reproduction",
                        "source_repo": "https://example.invalid/chess",
                    }
                )
            written = register_csv(csv_path, root)
            self.assertEqual(len(written), 1)
            out = written[0]
            self.assertTrue(out.name.startswith("spider2_external_baseline_chess_official_gpt-4.1"))
            payload = json.loads(out.read_text(encoding="utf-8"))
            rows = summary_rows_for_artifact(out, payload)
            self.assertEqual(rows[0]["implementation_type"], "official_external_reproduction")
            self.assertEqual(rows[0]["official_reproduction"], "True")
            self.assertEqual(rows[0]["baseline_reference"], "CHESS official")
            self.assertEqual(rows[0]["SER"], 75.0)
            self.assertIn(out.resolve(), resolve_inputs([str(root / "spider2*.json")]))

    def test_template_contains_required_external_baseline_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "template.csv"
            write_template(path)
            with path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                self.assertIn("official_reproduction", reader.fieldnames or [])
                self.assertIn("source_commit", reader.fieldnames or [])
                self.assertIn("baseline_reference", reader.fieldnames or [])
                rows = list(reader)
            self.assertEqual(rows[0]["implementation_type"], "official_external_reproduction")

    def test_legacy_registered_glob_is_resolved_for_summarize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "mac_sql_official_registered.json"
            legacy.write_text("{}\n", encoding="utf-8")
            self.assertIn(legacy.resolve(), resolve_inputs([str(root / "*_registered.json")]))


if __name__ == "__main__":
    unittest.main()
