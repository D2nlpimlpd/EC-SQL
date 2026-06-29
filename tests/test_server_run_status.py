from __future__ import annotations

import csv
import shutil
import unittest

from scripts.audit_goal_readiness import PROJECT_ROOT
from scripts.server_run_status import planned_artifact_status


class ServerRunStatusTests(unittest.TestCase):
    run_id = "unit_status_progress"

    def tearDown(self) -> None:
        run_dir = PROJECT_ROOT / "artifacts" / "server_runs" / self.run_id
        if run_dir.exists():
            shutil.rmtree(run_dir)

    def test_planned_artifact_status_reports_missing_enabled_artifacts(self) -> None:
        run_dir = PROJECT_ROOT / "artifacts" / "server_runs" / self.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        with (run_dir / "expected_artifacts.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["enabled", "stage", "artifact", "suite", "systems", "models", "expected_cases"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "enabled": "1",
                    "stage": "done_stage",
                    "artifact": "present.json",
                    "suite": "",
                    "systems": "",
                    "models": "",
                    "expected_cases": "1",
                }
            )
            writer.writerow(
                {
                    "enabled": "1",
                    "stage": "missing_stage",
                    "artifact": "missing.json",
                    "suite": "",
                    "systems": "",
                    "models": "",
                    "expected_cases": "1",
                }
            )
            writer.writerow(
                {
                    "enabled": "0",
                    "stage": "disabled_stage",
                    "artifact": "disabled.json",
                    "suite": "",
                    "systems": "",
                    "models": "",
                    "expected_cases": "1",
                }
            )
        (run_dir / "present.json").write_text("{}\n", encoding="utf-8")
        total, present, missing = planned_artifact_status(run_dir)
        self.assertEqual(total, 2)
        self.assertEqual(present, 1)
        self.assertEqual(missing, ["missing_stage: missing.json"])


if __name__ == "__main__":
    unittest.main()
