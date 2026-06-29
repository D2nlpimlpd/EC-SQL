from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_dataset_scale_report import (
    MIN_DBT_ROWS,
    MIN_MANIFEST_ROWS,
    MIN_SQLITE_ROWS,
    MIN_UNIQUE_INSTANCES,
)
from scripts.plan_server_matrix import expected_artifacts, full_profile_config


def default_out_dir(run_id: str) -> Path:
    return PROJECT_ROOT / "artifacts" / "server_runs" / run_id / "summary"


def contract_payload(run_id: str, out_dir: Path) -> dict[str, Any]:
    config = full_profile_config(run_id, PROJECT_ROOT / "artifacts" / "server_runs" / run_id)
    artifacts = expected_artifacts(config)
    enabled_artifacts = [row for row in artifacts if row.get("enabled") == "1"]
    return {
        "contract_name": "BoyueSQL Spider2 server acceptance contract",
        "run_id": run_id,
        "version": 1,
        "purpose": (
            "Define the machine-checkable evidence required before the generalized "
            "BoyueSQL Spider2/SOTA/ablation goal can be claimed complete."
        ),
        "dataset_scale_thresholds": {
            "manifest_rows_min": MIN_MANIFEST_ROWS,
            "unique_instances_min": MIN_UNIQUE_INSTANCES,
            "sqlite_rows_min": MIN_SQLITE_ROWS,
            "dbt_rows_min": MIN_DBT_ROWS,
            "evidence_files": [
                "artifacts/dataset_scale_report.json",
                "artifacts/dataset_scale_report.md",
                f"artifacts/server_runs/{run_id}/summary/server_{run_id}_dataset_scale_report.json",
                f"artifacts/server_runs/{run_id}/summary/server_{run_id}_dataset_scale_report.md",
            ],
        },
        "server_matrix_thresholds": {
            "sqlite_boyuesql_full_min_cases": 20,
            "sqlite_ablation_rows_min": 3,
            "sqlite_sota_style_baseline_rows_min": 4,
            "baseline_models_min": 3,
            "dbt_starter_baseline_cases_min": 68,
            "dbt_boyuesql_full_cases_min": 68,
            "dbt_ablation_rows_min": 5,
            "required_reports": [
                f"summary/server_{run_id}.csv",
                f"summary/server_{run_id}_cases.csv",
                f"summary/server_{run_id}.md",
                f"summary/server_{run_id}_failures.md",
                f"summary/server_{run_id}_evidence.csv",
                f"summary/server_{run_id}_evidence.md",
                f"summary/server_{run_id}_results_snippet.tex",
                f"summary/server_{run_id}_abstract.tex",
                f"summary/server_{run_id}_dataset_scale_report.json",
                f"summary/server_{run_id}_dataset_scale_report.md",
            ],
        },
        "required_commands": [
            "bash scripts/one_click_linux.sh preflight",
            "bash scripts/one_click_linux.sh setup",
            "bash scripts/one_click_linux.sh models",
            f"RUN_ID={run_id} bash scripts/one_click_linux.sh paper-run",
            f"RUN_ID={run_id} bash scripts/one_click_linux.sh validate",
            f"RUN_ID={run_id} bash scripts/one_click_linux.sh evidence",
            f"RUN_ID={run_id} bash scripts/one_click_linux.sh abstract",
            f"RUN_ID={run_id} bash scripts/one_click_linux.sh bundle",
            f"AUDIT_STRICT=1 SERVER_RUN_ID={run_id} bash scripts/one_click_linux.sh audit",
        ],
        "local_acceptance_commands": [
            (
                f"python scripts/finalize_server_result.py "
                f"artifacts/server_return/server_{run_id}_result_bundle.zip "
                f"--checksum artifacts/server_return/server_{run_id}_result_bundle.sha256 "
                f"--run-id {run_id} --overwrite "
                "--dataset-root D:\\text2sql_datasets\\Spider2 "
                "--manifest artifacts\\spider2_manifest.csv"
            ),
        ],
        "expected_artifacts": artifacts,
        "enabled_artifact_count": len(enabled_artifacts),
        "completion_rule": (
            "The goal is incomplete until every dataset-scale threshold is met, "
            "all enabled expected artifacts are present and non-empty, server "
            "matrix validation passes, the server-result abstract is generated, "
            "the returned result bundle is finalized locally by "
            "scripts/finalize_server_result.py, and strict readiness audit passes with "
            f"--server-run-id {run_id}."
        ),
        "out_dir": str(out_dir),
    }


def escape_md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def contract_markdown(payload: dict[str, Any]) -> str:
    run_id = payload["run_id"]
    lines = [
        f"# BoyueSQL Server Acceptance Contract: {run_id}",
        "",
        payload["purpose"],
        "",
        "## Dataset Scale Thresholds",
        "",
        "| Requirement | Threshold |",
        "| --- | ---: |",
    ]
    for key, value in payload["dataset_scale_thresholds"].items():
        if key == "evidence_files":
            continue
        lines.append(f"| `{escape_md(key)}` | {escape_md(value)} |")
    lines.extend(
        [
            "",
            "## Server Matrix Thresholds",
            "",
            "| Requirement | Threshold |",
            "| --- | ---: |",
        ]
    )
    for key, value in payload["server_matrix_thresholds"].items():
        if key == "required_reports":
            continue
        lines.append(f"| `{escape_md(key)}` | {escape_md(value)} |")
    lines.extend(["", "## Required Commands", "", "```bash"])
    lines.extend(payload["required_commands"])
    lines.extend(["```", "", "## Local Acceptance Commands", "", "```powershell"])
    lines.extend(payload["local_acceptance_commands"])
    lines.extend(["```", "", "## Enabled Expected Artifacts", ""])
    lines.extend(
        [
            "| Stage | Artifact | Suite | Systems | Models | Expected Cases |",
            "| --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for row in payload["expected_artifacts"]:
        if row.get("enabled") != "1":
            continue
        lines.append(
            "| "
            + " | ".join(
                escape_md(row.get(field, ""))
                for field in ["stage", "artifact", "suite", "systems", "models", "expected_cases"]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Completion Rule",
            "",
            payload["completion_rule"],
            "",
        ]
    )
    return "\n".join(lines)


def write_contract(payload: dict[str, Any], json_out: Path, md_out: Path) -> None:
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_out.write_text(contract_markdown(payload), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the BoyueSQL server acceptance contract.")
    parser.add_argument("--run-id", default=os.environ.get("RUN_ID", "server_full_spider2"))
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    args = parser.parse_args()

    out_dir = default_out_dir(args.run_id)
    json_out = Path(args.json_out) if args.json_out else out_dir / f"server_{args.run_id}_acceptance_contract.json"
    md_out = Path(args.md_out) if args.md_out else out_dir / f"server_{args.run_id}_acceptance_contract.md"
    payload = contract_payload(args.run_id, out_dir)
    write_contract(payload, json_out, md_out)
    print(f"[acceptance-contract] json: {json_out.resolve()}")
    print(f"[acceptance-contract] markdown: {md_out.resolve()}")
    print(f"[acceptance-contract] enabled_artifacts={payload['enabled_artifact_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
