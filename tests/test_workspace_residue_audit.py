from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.audit_workspace_residue import quarantine_findings, scan_workspace, write_report


class WorkspaceResidueAuditTests(unittest.TestCase):
    def test_scan_reports_private_legacy_files_outside_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ase.tex").write_text(
                "Oracle 11g " + "health" + "-examination " + "EXAM" + "_RECORD\n",
                encoding="utf-8",
            )
            (root / "artifacts").mkdir()
            (root / "artifacts" / "ase.tex").write_text("EXAM" + "_RECORD\n", encoding="utf-8")

            findings = scan_workspace(root)

            paths = {finding.path for finding in findings}
            self.assertIn("ase.tex", paths)
            self.assertNotIn("artifacts/ase.tex", paths)
            self.assertTrue(any(finding.reason == "legacy_private_filename" for finding in findings))
            self.assertTrue(any(("EXAM" + "_") in finding.match for finding in findings))

    def test_report_and_quarantine_preserve_legacy_files_under_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "backup.tex"
            source.write_text("entry" + "-exit inspection" + " and quarantine\n", encoding="utf-8")
            findings = scan_workspace(root)
            target = root / "artifacts" / "legacy_workspace_residue"

            moved = quarantine_findings(root, findings, target)
            remaining = scan_workspace(root)
            out_json = root / "artifacts" / "report.json"
            out_md = root / "artifacts" / "report.md"
            write_report(remaining, out_json, out_md)

            self.assertEqual(moved, ["backup.tex"])
            self.assertFalse(source.exists())
            self.assertTrue((target / "backup.tex").exists())
            self.assertEqual(remaining, [])
            self.assertIn('"status": "PASS"', out_json.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
