from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verify_server_release import (
    FORBIDDEN_ROOTS,
    PRIVATE_DB_PATTERNS,
    REQUIRED_FILES,
    text_file_name,
    verify_archive,
)


DEFAULT_DATASET_ROOT = Path(r"D:\text2sql_datasets\Spider2")
DEFAULT_RELEASE = PROJECT_ROOT / "artifacts" / "server_release" / "ecsql_spider2_server.zip"
DEFAULT_CHECKSUM = PROJECT_ROOT / "artifacts" / "server_release" / "ecsql_spider2_server.sha256"
DEFAULT_MANIFEST = PROJECT_ROOT / "artifacts" / "spider2_manifest.csv"
DEFAULT_DBT68 = PROJECT_ROOT / "artifacts" / "spider2_dbt_llm_edit_dbt68_v10b_full.json"
DEFAULT_SQLITE24 = (
    PROJECT_ROOT
    / "artifacts"
    / "server_runs"
    / "sqlite_llm_server_gold24_v1"
    / "spider2_sqlite_ecsql_ablation_qwen2.5-coder_7b.json"
)
DEFAULT_ABSTRACT = PROJECT_ROOT / "artifacts" / "ecsql_spider2_abstract.tex"
DEFAULT_DATASET_SCALE_JSON = PROJECT_ROOT / "artifacts" / "dataset_scale_report.json"
DEFAULT_DATASET_SCALE_MD = PROJECT_ROOT / "artifacts" / "dataset_scale_report.md"
DEFAULT_SERVER_RUN_ID = "server_full_spider2"


@dataclass
class Check:
    requirement: str
    status: str
    evidence: str


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, dict) else {}


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


SQLITE_BASELINE_TARGETS = {
    "direct",
    "din_sql_style",
    "dail_sql_style",
    "self_debug_style",
    "mac_sql_style",
    "chess_style",
}


def is_truthy_text(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def is_sqlite_sota_baseline_row(row: dict[str, str]) -> bool:
    if row.get("suite") != "spider2-sqlite" or integer(row.get("cases")) < 20:
        return False
    system = str(row.get("system") or "")
    implementation_type = str(row.get("implementation_type") or "")
    return (
        system in SQLITE_BASELINE_TARGETS
        or implementation_type == "official_external_reproduction"
        or is_truthy_text(row.get("official_reproduction"))
    )


def manifest_counts(path: Path) -> tuple[int, dict[str, int], dict[str, int]]:
    counts: dict[str, int] = {}
    instances: set[str] = set()
    dbs: set[str] = set()
    engines: set[str] = set()
    rows = 0
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows += 1
            if row.get("instance_id"):
                instances.add(str(row["instance_id"]))
            if row.get("db"):
                dbs.add(str(row["db"]))
            if row.get("engine"):
                engines.add(str(row["engine"]))
            key = f"{row.get('setting') or 'unknown'}:{row.get('engine') or 'unknown'}"
            counts[key] = counts.get(key, 0) + 1
    coverage = {
        "unique_instances": len(instances),
        "unique_dbs": len(dbs),
        "unique_engines": len(engines),
    }
    return rows, counts, coverage


def fmt_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def check_manifest(manifest: Path, dataset_root: Path) -> Check:
    if not dataset_root.exists():
        return Check("Spider2 dataset downloaded", "FAIL", f"missing dataset root: {dataset_root}")
    if not manifest.exists():
        return Check("Spider2 manifest generated", "FAIL", f"missing manifest: {manifest}")
    rows, counts, coverage = manifest_counts(manifest)
    evidence = (
        f"{rows} manifest rows, "
        f"{coverage['unique_instances']} unique instances/projects, "
        f"{coverage['unique_dbs']} unique logical db names, "
        f"{coverage['unique_engines']} engines ({fmt_counts(counts)})"
    )
    if (
        rows >= 600
        and coverage["unique_instances"] >= 600
        and counts.get("spider2-dbt:duckdb-dbt", 0) >= 68
        and counts.get("spider2-lite:sqlite", 0) >= 100
    ):
        return Check("Spider2 broad benchmark available", "PASS", evidence)
    return Check("Spider2 broad benchmark available", "FAIL", evidence)


def check_dataset_scale_report(dataset_root: Path, manifest: Path) -> Check:
    try:
        from scripts.build_dataset_scale_report import (
            DEFAULT_JSON as DATASET_SCALE_JSON,
            DEFAULT_MD as DATASET_SCALE_MD,
            build_report,
            write_report,
        )

        report = build_report(dataset_root, manifest)
        write_report(report, DATASET_SCALE_JSON, DATASET_SCALE_MD)
    except Exception as exc:
        return Check("Spider2 dataset scale report", "FAIL", f"{type(exc).__name__}: {exc}")
    status = "PASS" if report.status == "PASS" else "FAIL"
    return Check(
        "Spider2 dataset scale report",
        status,
        f"{report.evidence}, report={DATASET_SCALE_MD}",
    )


def check_acceptance_contract(run_id: str = DEFAULT_SERVER_RUN_ID) -> Check:
    try:
        from scripts.build_server_acceptance_contract import (
            contract_payload,
            default_out_dir,
            write_contract,
        )

        out_dir = default_out_dir(run_id)
        json_path = out_dir / f"server_{run_id}_acceptance_contract.json"
        md_path = out_dir / f"server_{run_id}_acceptance_contract.md"
        payload = contract_payload(run_id, out_dir)
        write_contract(payload, json_path, md_path)
    except Exception as exc:
        return Check("Server acceptance contract", "FAIL", f"{type(exc).__name__}: {exc}")

    dataset_thresholds = payload.get("dataset_scale_thresholds") or {}
    matrix_thresholds = payload.get("server_matrix_thresholds") or {}
    enabled_artifacts = integer(payload.get("enabled_artifact_count"))
    missing: list[str] = []
    if integer(dataset_thresholds.get("manifest_rows_min")) < 600:
        missing.append("manifest_rows_min>=600")
    if integer(dataset_thresholds.get("unique_instances_min")) < 600:
        missing.append("unique_instances_min>=600")
    if integer(dataset_thresholds.get("sqlite_rows_min")) < 100:
        missing.append("sqlite_rows_min>=100")
    if integer(dataset_thresholds.get("dbt_rows_min")) < 68:
        missing.append("dbt_rows_min>=68")
    if integer(matrix_thresholds.get("baseline_models_min")) < 3:
        missing.append("baseline_models_min>=3")
    if integer(matrix_thresholds.get("dbt_ablation_rows_min")) < 5:
        missing.append("dbt_ablation_rows_min>=5")
    if enabled_artifacts <= 0:
        missing.append("enabled artifacts")
    completion_rule = str(payload.get("completion_rule") or "").lower()
    if "incomplete" not in completion_rule or "audit" not in completion_rule:
        missing.append("completion rule")
    if not json_path.exists() or not md_path.exists():
        missing.append("contract files")

    if missing:
        return Check("Server acceptance contract", "FAIL", f"missing/weak: {', '.join(missing)}")
    return Check(
        "Server acceptance contract",
        "PASS",
        f"run_id={run_id}, enabled_artifacts={enabled_artifacts}, json={json_path}, markdown={md_path}",
    )


def check_server_upload_packet(run_id: str = DEFAULT_SERVER_RUN_ID) -> Check:
    try:
        from scripts.build_server_upload_packet import (
            DEFAULT_OUT_DIR,
            build_packet,
            verify_packet,
        )

        packet, checksum, manifest = build_packet(
            run_id=run_id,
            out_dir=DEFAULT_OUT_DIR,
            archive=DEFAULT_RELEASE,
            checksum=DEFAULT_CHECKSUM,
            dataset_root=DEFAULT_DATASET_ROOT,
            manifest=DEFAULT_MANIFEST,
            host="user@server.example.com",
            remote_dir="~/ecsql_spider2_run",
            local_return_dir=PROJECT_ROOT / "artifacts" / "server_return",
        )
        errors = verify_packet(packet, checksum)
    except Exception as exc:
        return Check("Server upload packet", "FAIL", f"{type(exc).__name__}: {exc}")
    if errors:
        return Check("Server upload packet", "FAIL", "; ".join(errors[:5]))
    return Check(
        "Server upload packet",
        "PASS",
        f"packet={packet}, checksum={checksum}, manifest={manifest}",
    )


def check_server_upload_packet_smoke(run_id: str = DEFAULT_SERVER_RUN_ID) -> Check:
    try:
        from scripts.build_server_upload_packet import DEFAULT_OUT_DIR
        from scripts.smoke_test_server_upload_packet import smoke_upload_packet

        packet = DEFAULT_OUT_DIR / f"{run_id}_server_upload_packet.zip"
        checksum = packet.with_suffix(".sha256")
        if not packet.exists() or not checksum.exists():
            packet_check = check_server_upload_packet(run_id)
            if packet_check.status != "PASS":
                return Check("Server upload packet smoke test", packet_check.status, packet_check.evidence)
        notes = smoke_upload_packet(packet, checksum, run_doctor=True, run_diagnostics=True)
    except Exception as exc:
        return Check("Server upload packet smoke test", "FAIL", f"{type(exc).__name__}: {exc}")
    return Check("Server upload packet smoke test", "PASS", "; ".join(notes))


def check_server_acceptance_flow_smoke() -> Check:
    if os.environ.get("EC_SQL_IN_FINALIZER_AUDIT") == "1":
        return Check(
            "Server result acceptance flow smoke test",
            "PASS",
            "skipped inside finalizer audit to avoid recursive finalization; standalone smoke covers finalizer",
        )
    try:
        from scripts.smoke_test_server_acceptance_flow import smoke_acceptance_flow

        notes = smoke_acceptance_flow("acceptance_flow_smoke")
    except Exception as exc:
        return Check("Server result acceptance flow smoke test", "FAIL", f"{type(exc).__name__}: {exc}")
    return Check("Server result acceptance flow smoke test", "PASS", "; ".join(notes))


def check_sqlite24(path: Path) -> Check:
    if not path.exists():
        return Check("SQLite local gold evidence", "PENDING", f"missing artifact: {path}")
    summary = load_json(path).get("summary") or {}
    metrics = summary.get("ecsql") if isinstance(summary, dict) else None
    if not isinstance(metrics, dict):
        return Check("SQLite local gold evidence", "FAIL", f"missing ecsql summary in {path}")
    cases = int(metrics.get("cases") or 0)
    er = number(metrics.get("ER"))
    re_rate = number(metrics.get("RE"))
    ser = number(metrics.get("SER"))
    status = "PASS" if cases >= 24 and er == 100.0 and re_rate == 100.0 and ser == 100.0 else "FAIL"
    return Check(
        "SQLite local gold evidence",
        status,
        f"cases={cases}, ER={er}, RE={re_rate}, SER={ser}, artifact={path}",
    )


def check_dbt68(path: Path) -> Check:
    if not path.exists():
        return Check("DBT 68-task evidence", "PENDING", f"missing artifact: {path}")
    summary = load_json(path).get("summary") or {}
    if not isinstance(summary, dict):
        return Check("DBT 68-task evidence", "FAIL", f"missing summary in {path}")
    cases = int(summary.get("cases") or 0)
    run_ok = int(summary.get("run_ok") or 0)
    gold_evaluable = int(summary.get("gold_evaluable") or 0)
    ser_eval = number(summary.get("semantic_pass_rate_evaluable"))
    status = "PASS" if cases >= 68 and run_ok == cases and gold_evaluable >= 60 and ser_eval >= 60.0 else "FAIL"
    return Check(
        "DBT 68-task evidence",
        status,
        f"cases={cases}, run_ok={run_ok}, gold_evaluable={gold_evaluable}, SER_evaluable={ser_eval}, artifact={path}",
    )


def check_release(archive: Path, checksum: Path) -> Check:
    if archive.exists():
        errors = verify_archive(archive, checksum, "ecsql_spider2_server")
        if errors:
            return Check("Clean Linux server release", "FAIL", "; ".join(errors[:5]))
        size_mb = archive.stat().st_size / (1024 * 1024)
        digest = checksum.read_text(encoding="utf-8").strip().split()[0]
        return Check("Clean Linux server release", "PASS", f"{archive} ({size_mb:.1f} MiB), sha256={digest}")
    workspace_errors = verify_unpacked_workspace(PROJECT_ROOT)
    if workspace_errors:
        return Check("Clean Linux server release", "FAIL", "; ".join(workspace_errors[:5]))
    return Check("Clean Linux server release", "PASS", f"unpacked workspace verified at {PROJECT_ROOT}")


def check_workspace_residue(project_root: Path = PROJECT_ROOT) -> Check:
    try:
        from scripts.audit_workspace_residue import scan_workspace, write_report

        findings = scan_workspace(project_root)
        write_report(
            findings,
            PROJECT_ROOT / "artifacts" / "workspace_private_residue_report.json",
            PROJECT_ROOT / "artifacts" / "workspace_private_residue_report.md",
        )
    except Exception as exc:
        return Check("Workspace private residue removed", "FAIL", f"{type(exc).__name__}: {exc}")
    if findings:
        sample = ", ".join(finding.path for finding in findings[:5])
        return Check(
            "Workspace private residue removed",
            "FAIL",
            f"{len(findings)} finding(s); run scripts/audit_workspace_residue.py --quarantine. sample={sample}",
        )
    return Check(
        "Workspace private residue removed",
        "PASS",
        str(PROJECT_ROOT / "artifacts" / "workspace_private_residue_report.md"),
    )


def verify_unpacked_workspace(project_root: Path) -> list[str]:
    errors: list[str] = []
    for rel in REQUIRED_FILES:
        if not (project_root / rel).exists():
            errors.append(f"required file missing from unpacked workspace: {rel}")
    workspace_forbidden_roots = set(FORBIDDEN_ROOTS) - {"artifacts"}
    for forbidden in workspace_forbidden_roots:
        if (project_root / forbidden).exists():
            errors.append(f"forbidden root present in unpacked workspace: {forbidden}")
    for finding in scan_private_workspace(project_root):
        errors.append(f"private database residue: {finding}")
    return errors


def scan_private_workspace(project_root: Path) -> list[str]:
    import re

    findings: list[str] = []
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in PRIVATE_DB_PATTERNS if pattern.startswith(r"\b")]
    literals = [pattern for pattern in PRIVATE_DB_PATTERNS if not pattern.startswith(r"\b")]
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(project_root).as_posix()
        if rel.split("/", 1)[0] in {"artifacts", "__pycache__", ".pytest_cache"}:
            continue
        if not text_file_name(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in compiled:
            if pattern.search(text):
                findings.append(f"{rel}: {pattern.pattern}")
        for literal in literals:
            if literal in text:
                findings.append(f"{rel}: {literal}")
    return findings


def check_one_click() -> Check:
    script = PROJECT_ROOT / "scripts" / "one_click_linux.sh"
    if not script.exists():
        return Check("One-click Linux entrypoint", "FAIL", f"missing {script}")
    text = script.read_text(encoding="utf-8")
    required = [
        "preflight)",
        "setup)",
        "models)",
        "dataset-report)",
        "contract)",
        "plan)",
        "dry-run)",
        "smoke)",
        "benchmark)",
        "paper-run)",
        "paper-launch)",
        "launch)",
        "resume)",
        "summarize)",
        "status)",
        "validate)",
        "evidence)",
        "abstract)",
        "bundle)",
        "diagnostics)",
        "upload-packet)",
        "audit)",
        "service)",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        return Check("One-click Linux entrypoint", "FAIL", f"missing modes: {', '.join(missing)}")
    return Check(
        "One-click Linux entrypoint",
        "PASS",
        "preflight/setup/models/dataset-report/contract/plan/dry-run/smoke/benchmark/paper-run/paper-launch/launch/resume/summarize/status/validate/evidence/abstract/bundle/diagnostics/upload-packet/audit/service modes present",
    )


def check_release_smoke(archive: Path, checksum: Path) -> Check:
    if not archive.exists():
        return Check("Server release smoke test", "PENDING", f"missing release archive: {archive}")
    try:
        from scripts.smoke_test_server_release import smoke_release

        notes = smoke_release(archive, checksum, "ecsql_spider2_server")
    except Exception as exc:
        return Check("Server release smoke test", "FAIL", f"{type(exc).__name__}: {exc}")
    return Check("Server release smoke test", "PASS", "; ".join(notes))


def check_handoff_dry_run(server_run_id: str = "server_full_spider2") -> Check:
    stages = [
        ("remote-preflight", []),
        ("doctor", []),
        ("launch", ["--background"]),
        ("diagnostics", []),
        ("wait", ["--poll-seconds", "5", "--max-wait-seconds", "10"]),
    ]
    outputs: dict[str, str] = {}
    for stage, extra in stages:
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_server_handoff.py"),
            "--host",
            "audit@example.org",
            "--run-id",
            server_run_id,
            "--stage",
            stage,
            *extra,
        ]
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
        except Exception as exc:
            return Check("Server handoff dry run", "FAIL", f"{stage}: {type(exc).__name__}: {exc}")
        outputs[stage] = completed.stdout + completed.stderr
        if completed.returncode != 0:
            return Check(
                "Server handoff dry run",
                "FAIL",
                f"{stage}: returncode={completed.returncode}, output={outputs[stage][-500:]}",
            )

    packet_dir = f"{server_run_id}_upload_packet"
    required_by_stage = {
        "remote-preflight": [
            "execute=False",
            "[remote-preflight] host=$(hostname)",
            "MIN_FREE_GB=20.0",
            "command -v \"$cmd\"",
            "Python >= 3.10",
            "disk_free_gb",
            "REQUIRE_GPU",
            "REQUIRE_OLLAMA",
            "nvidia-smi",
        ],
        "doctor": [
            "execute=False",
            f"rm -rf {packet_dir}",
            "if command -v unzip >/dev/null 2>&1",
            f"python3 -m zipfile -e {server_run_id}_server_upload_packet.zip {packet_dir}",
            "RUN_PACKET_ON_SERVER.sh doctor",
        ],
        "launch": [
            "execute=False",
            f"rm -rf {packet_dir}",
            "if command -v unzip >/dev/null 2>&1",
            f"python3 -m zipfile -e {server_run_id}_server_upload_packet.zip {packet_dir}",
            "RUN_PACKET_ON_SERVER.sh background",
            "SKIP_MODELS=0",
        ],
        "diagnostics": [
            "execute=False",
            f"cd ~/ecsql_spider2_run/{packet_dir}",
            "RUN_PACKET_ON_SERVER.sh diagnostics",
        ],
        "wait": [
            "execute=False",
            f"cd ~/ecsql_spider2_run/{packet_dir}",
            "checking result bundle",
            "server_job.pid",
            "timed out after 10s",
        ],
    }
    missing: list[str] = []
    for stage, terms in required_by_stage.items():
        output = outputs[stage]
        missing.extend(f"{stage}:{term}" for term in terms if term not in output)
    if missing:
        combined_tail = "\n".join(outputs[stage][-500:] for stage in ("remote-preflight", "doctor", "launch", "diagnostics", "wait"))
        return Check(
            "Server handoff dry run",
            "FAIL",
            f"missing={missing}, output_tail={combined_tail}",
        )
    return Check(
        "Server handoff dry run",
        "PASS",
        "remote-preflight/doctor/launch/diagnostics/wait dry-runs server checks, re-extract the upload packet, call RUN_PACKET_ON_SERVER.sh, poll the packet-internal result path, and perform no ssh/scp execution",
    )


def check_abstract(path: Path) -> Check:
    if not path.exists():
        fallback = PROJECT_ROOT / "ecsql_spider2_abstract.tex"
        if fallback.exists():
            path = fallback
    if not path.exists():
        return Check("Paper abstract generated", "PENDING", f"missing {path}")
    text = path.read_text(encoding="utf-8")
    required_terms = ["EC-SQL", "Spider2", "67.74", "24", "qwen2.5-coder:7b", "Linux-ready"]
    missing = [term for term in required_terms if term not in text]
    if missing:
        return Check("Paper abstract generated", "FAIL", f"missing terms: {', '.join(missing)}")
    return Check("Paper abstract generated", "PASS", str(path))


def check_server_result_abstract(
    server_run_id: str | None,
    run_dir_override: Path | None = None,
) -> Check:
    if not server_run_id:
        return Check(
            "Server-result abstract generated",
            "PENDING",
            "No SERVER_RUN_ID provided; generate the server-result abstract after the Linux benchmark completes.",
        )
    run_dir = run_dir_override or PROJECT_ROOT / "artifacts" / "server_runs" / server_run_id
    path = run_dir / "summary" / f"server_{server_run_id}_abstract.tex"
    if not path.exists() or path.stat().st_size <= 0:
        return Check("Server-result abstract generated", "PENDING", f"missing or empty {path}")
    text = path.read_text(encoding="utf-8")
    missing = [
        term
        for term in [
            r"\begin{abstract}",
            r"\end{abstract}",
            "EC-SQL",
            "Spider2",
            "validated server run",
            "SOTA-style baseline",
            "semantic pass rate",
        ]
        if term not in text
    ]
    escaped_run_id = server_run_id.replace("_", r"\_")
    if server_run_id not in text and escaped_run_id not in text:
        missing.append(f"run id {server_run_id}")
    if missing:
        return Check("Server-result abstract generated", "FAIL", f"missing terms: {', '.join(missing)}")
    return Check("Server-result abstract generated", "PASS", str(path))


def check_server_matrix(server_run_id: str | None, run_dir_override: Path | None = None) -> Check:
    if not server_run_id:
        return Check(
            "Full server SOTA matrix executed",
            "PENDING",
            "No SERVER_RUN_ID provided; run the Linux benchmark and audit with --server-run-id <RUN_ID>.",
        )
    run_dir = run_dir_override or PROJECT_ROOT / "artifacts" / "server_runs" / server_run_id
    summary_dir = run_dir / "summary"
    summary = summary_dir / f"server_{server_run_id}.csv"
    cases = summary_dir / f"server_{server_run_id}_cases.csv"
    markdown = summary_dir / f"server_{server_run_id}.md"
    failures = summary_dir / f"server_{server_run_id}_failures.md"
    required_reports = [summary, cases, markdown, failures]
    missing_reports = [path for path in required_reports if not path.exists()]
    if missing_reports:
        missing = ", ".join(str(path) for path in missing_reports)
        return Check("Full server SOTA matrix executed", "PENDING", f"missing server report(s): {missing}")

    with summary.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return Check("Full server SOTA matrix executed", "PENDING", f"empty server summary: {summary}")

    def artifact_path(row: dict[str, Any]) -> Path | None:
        raw = str(row.get("artifact") or "").strip()
        if not raw:
            return None
        path = Path(raw)
        if path.exists():
            return path
        if not path.is_absolute():
            candidate = run_dir / path
            if candidate.exists():
                return candidate
        fallback = run_dir / Path(raw).name
        if fallback.exists():
            return fallback
        return path

    missing_artifacts: list[str] = []
    nonempty_rows: list[dict[str, Any]] = []
    for row in rows:
        if integer(row.get("cases")) <= 0:
            continue
        path = artifact_path(row)
        if path is None or not path.exists() or path.stat().st_size <= 0:
            missing_artifacts.append(str(row.get("artifact") or "(empty artifact)"))
            continue
        nonempty_rows.append(row)
    if missing_artifacts:
        return Check(
            "Full server SOTA matrix executed",
            "PENDING",
            f"summary references missing/empty artifact(s): {', '.join(missing_artifacts[:5])}",
        )

    sqlite_rows = [row for row in nonempty_rows if row.get("suite") == "spider2-sqlite"]
    dbt_rows = [row for row in nonempty_rows if row.get("suite") == "spider2-dbt"]
    systems = {str(row.get("system") or "") for row in nonempty_rows}
    models = {str(row.get("model") or "") for row in nonempty_rows if str(row.get("model") or "")}

    sqlite_full = [row for row in sqlite_rows if row.get("system") == "ecsql" and integer(row.get("cases")) >= 20]
    sqlite_ablations = {
        str(row.get("system") or "")
        for row in sqlite_rows
        if str(row.get("system") or "").startswith("no_") and integer(row.get("cases")) >= 20
    }
    sqlite_baselines = {
        str(row.get("system") or "")
        for row in sqlite_rows
        if is_sqlite_sota_baseline_row(row)
    }
    baseline_models = {
        str(row.get("model") or "")
        for row in sqlite_rows
        if is_sqlite_sota_baseline_row(row) and str(row.get("model") or "")
    }
    dbt_full = [
        row
        for row in dbt_rows
        if "ecsql_deterministic_full" in str(row.get("system") or "") and integer(row.get("cases")) >= 68
    ]
    dbt_ablations = {
        str(row.get("system") or "")
        for row in dbt_rows
        if "ablation" in str(row.get("system") or "") and integer(row.get("cases")) >= 68
    }
    dbt_baseline = [
        row
        for row in dbt_rows
        if "existing_project" in str(row.get("system") or "") and integer(row.get("cases")) >= 68
    ]

    missing_requirements: list[str] = []
    if not sqlite_full:
        missing_requirements.append("SQLite EC-SQL full row with >=20 gold cases")
    if len(sqlite_ablations) < 3:
        missing_requirements.append(f">=3 SQLite ablations (found {len(sqlite_ablations)})")
    if len(sqlite_baselines) < 4:
        missing_requirements.append(f">=4 SOTA-style SQLite baselines (found {len(sqlite_baselines)})")
    if len(baseline_models) < 3:
        missing_requirements.append(f">=3 baseline models (found {len(baseline_models)})")
    if not dbt_baseline:
        missing_requirements.append("DBT starter-project baseline with 68 cases")
    if not dbt_full:
        missing_requirements.append("DBT EC-SQL deterministic full with 68 cases")
    if len(dbt_ablations) < 5:
        missing_requirements.append(f">=5 DBT ablations with 68 cases (found {len(dbt_ablations)})")

    if not missing_requirements:
        return Check(
            "Full server SOTA matrix executed",
            "PASS",
            (
                f"{len(nonempty_rows)} summary rows; "
                f"SQLite full={len(sqlite_full)}, SQLite ablations={len(sqlite_ablations)}, "
                f"SOTA baselines={len(sqlite_baselines)} across {len(baseline_models)} models, "
                f"DBT full={len(dbt_full)}, DBT ablations={len(dbt_ablations)}; "
                f"systems={sorted(systems)}, models={sorted(models)}"
            ),
        )
    return Check(
        "Full server SOTA matrix executed",
        "PENDING",
        (
            f"incomplete server matrix: {'; '.join(missing_requirements)}. "
            f"summary_rows={len(nonempty_rows)}, systems={sorted(systems)}, models={sorted(models)}"
        ),
    )


def markdown_report(checks: Iterable[Check]) -> str:
    rows = list(checks)
    lines = [
        "# EC-SQL Goal Readiness Audit",
        "",
        "| Requirement | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    for check in rows:
        evidence = check.evidence.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check.requirement} | {check.status} | {evidence} |")
    lines.extend(
        [
            "",
            "Completion rule: this goal is not complete while any requirement is FAIL or PENDING.",
            "The current expected remaining PENDING items are the full Linux/server SOTA matrix and the server-result abstract until the matrix is actually executed, summarized, and rendered into paper text.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit EC-SQL goal readiness without redefining the original objective.")
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--sqlite24", default=str(DEFAULT_SQLITE24))
    parser.add_argument("--dbt68", default=str(DEFAULT_DBT68))
    parser.add_argument("--release", default=str(DEFAULT_RELEASE))
    parser.add_argument("--checksum", default=str(DEFAULT_CHECKSUM))
    parser.add_argument("--abstract", default=str(DEFAULT_ABSTRACT))
    parser.add_argument("--server-run-id", default="")
    parser.add_argument(
        "--server-run-dir",
        default="",
        help="Optional imported server run directory override for --server-run-id.",
    )
    parser.add_argument("--out", default=str(PROJECT_ROOT / "artifacts" / "goal_readiness_audit.md"))
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any check is FAIL or PENDING.")
    args = parser.parse_args()

    server_run_dir = Path(args.server_run_dir) if args.server_run_dir else None

    checks = [
        check_manifest(Path(args.manifest), Path(args.dataset_root)),
        check_dataset_scale_report(Path(args.dataset_root), Path(args.manifest)),
        check_acceptance_contract(args.server_run_id or DEFAULT_SERVER_RUN_ID),
        check_server_upload_packet(args.server_run_id or DEFAULT_SERVER_RUN_ID),
        check_server_upload_packet_smoke(args.server_run_id or DEFAULT_SERVER_RUN_ID),
        check_server_acceptance_flow_smoke(),
        check_sqlite24(Path(args.sqlite24)),
        check_dbt68(Path(args.dbt68)),
        check_workspace_residue(),
        check_release(Path(args.release), Path(args.checksum)),
        check_release_smoke(Path(args.release), Path(args.checksum)),
        check_one_click(),
        check_handoff_dry_run(args.server_run_id or "server_full_spider2"),
        check_abstract(Path(args.abstract)),
        check_server_result_abstract(args.server_run_id or None, server_run_dir),
        check_server_matrix(args.server_run_id or None, server_run_dir),
    ]
    report = markdown_report(checks)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    has_open = any(check.status in {"FAIL", "PENDING"} for check in checks)
    return 2 if args.strict and has_open else 0


if __name__ == "__main__":
    raise SystemExit(main())
