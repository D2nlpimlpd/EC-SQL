from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "boyuesql_service.py",
    "boyuesql_generic/dialects.py",
    "boyuesql_generic/dictionary.py",
    "boyuesql_generic/connections.py",
    "scripts/setup_linux.sh",
    "scripts/one_click_linux.sh",
    "scripts/server_preflight.py",
    "scripts/check_linux_shell_scripts.py",
    "scripts/audit_goal_readiness.py",
    "scripts/audit_workspace_residue.py",
    "scripts/pull_ollama_models.py",
    "scripts/download_hf_models.py",
    "scripts/plan_server_matrix.py",
    "scripts/launch_server_benchmark.sh",
    "scripts/server_run_status.py",
    "scripts/build_server_diagnostics.py",
    "scripts/validate_server_matrix.py",
    "scripts/build_server_evidence_report.py",
    "scripts/build_server_abstract.py",
    "scripts/build_server_result_bundle.py",
    "scripts/verify_server_result_bundle.py",
    "scripts/import_server_result_bundle.py",
    "scripts/finalize_server_result.py",
    "scripts/baseline_manifest.py",
    "scripts/register_external_baseline_result.py",
    "scripts/register_external_baseline_results.py",
    "scripts/start_linux.sh",
    "scripts/run_server_experiments.sh",
    "scripts/run_full_server_benchmark.sh",
    "scripts/build_server_release.py",
    "scripts/build_server_submission_manifest.py",
    "scripts/build_server_handoff_commands.py",
    "scripts/run_server_handoff.py",
    "scripts/build_dataset_scale_report.py",
    "scripts/build_server_acceptance_contract.py",
    "scripts/build_server_upload_packet.py",
    "scripts/smoke_test_server_upload_packet.py",
    "scripts/smoke_test_server_acceptance_flow.py",
    "scripts/verify_server_release.py",
    "scripts/smoke_test_server_release.py",
    "third_party/raganything-1.3.1/raganything/sql_dictionary.py",
    "SERVER_RELEASE_MANIFEST.txt",
    "SERVER_RUNBOOK.md",
    "SERVER_MODEL_GUIDE.md",
    "boyuesql_spider2_abstract.tex",
    "requirements-oracle.txt",
    "constraints-server.txt",
    "baselines/baseline_manifest.json",
    ".env.example",
    "Dockerfile",
    "docker-compose.yml",
]

FORBIDDEN_ROOTS = {
    "main.py",
    "templates",
    "static",
    "artifacts",
    "pdf_extracted_images",
    "pdf_page_renders",
}

ALLOWED_TEX_FILES = {
    "boyuesql_spider2_abstract.tex",
}

FORBIDDEN_RELEASE_FILENAMES = {
    "ase.tex",
    "ase_raganything_updated.tex",
    "ase_raganything_updated_zh.tex",
    "backup.tex",
    "boyuesql_icdm_merged_figures.tex",
    "boyuesql_icdm_merged_figures_package.zip",
    "overleaf.tex",
    "paper_multiagent_nl2sql.tex",
    "figure1_boyuesql.pdf",
    "figure1_boyuesql.png",
    "figure1_boyuesql.svg",
    "schema_kg.pdf",
    "schema_kg.png",
    "xiezuo.pdf",
    "xiezuo.png",
    "xiezuo.svg",
}

PRIVATE_DB_PATTERNS = [
    r"\bEXAM_",
    r"\bCONTROL_STATUS\b",
    r"\bFEE_RECORD\b",
    r"\bVIEW_FEE\b",
    r"\bischinnese\b",
    r"\bisnormal\b",
    r"\bfee_type\b",
    "体" + "检",
    "检验" + "检疫",
    "出入" + "境",
    "health" + "-examination",
    "entry" + "-exit",
    "inspection" + " and quarantine",
    "municipal " + "entry" + "-exit",
    "Oracle " + "智能查询",
    "Oracle " + "内网访问",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_checksum(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8").strip()
    parts = text.split()
    if len(parts) < 2:
        raise ValueError(f"invalid checksum file: {path}")
    return parts[0], parts[1]


def normalized_names(zf: zipfile.ZipFile, prefix: str) -> set[str]:
    names: set[str] = set()
    prefix = prefix.strip("/").replace("\\", "/")
    for raw_name in zf.namelist():
        name = raw_name.replace("\\", "/")
        if prefix and name.startswith(prefix + "/"):
            name = name[len(prefix) + 1 :]
        names.add(name.strip("/"))
    return names


def text_file_name(name: str) -> bool:
    suffix = Path(name).suffix.lower()
    return suffix in {
        "",
        ".cfg",
        ".csv",
        ".env",
        ".json",
        ".md",
        ".py",
        ".sh",
        ".txt",
        ".toml",
        ".yml",
        ".yaml",
    } or Path(name).name in {"Dockerfile", ".env.example", ".gitignore", ".dockerignore"}


def scan_private_patterns(zf: zipfile.ZipFile, prefix: str) -> list[str]:
    findings: list[str] = []
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in PRIVATE_DB_PATTERNS if pattern.startswith(r"\b")]
    literals = [pattern for pattern in PRIVATE_DB_PATTERNS if not pattern.startswith(r"\b")]
    for raw_name in zf.namelist():
        name = raw_name.replace("\\", "/")
        logical = name
        if prefix and logical.startswith(prefix + "/"):
            logical = logical[len(prefix) + 1 :]
        if not text_file_name(logical):
            continue
        try:
            text = zf.read(raw_name).decode("utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in compiled:
            if pattern.search(text):
                findings.append(f"{logical}: {pattern.pattern}")
        for literal in literals:
            if literal in text:
                findings.append(f"{logical}: {literal}")
    return findings


def verify_archive(archive: Path, checksum_path: Path, prefix: str) -> list[str]:
    errors: list[str] = []
    if not archive.exists():
        return [f"missing archive: {archive}"]
    if not checksum_path.exists():
        errors.append(f"missing checksum: {checksum_path}")
    else:
        expected_hash, expected_name = read_checksum(checksum_path)
        actual_hash = sha256(archive)
        if actual_hash != expected_hash:
            errors.append(f"checksum mismatch: expected {expected_hash}, got {actual_hash}")
        if expected_name != archive.name:
            errors.append(f"checksum archive name mismatch: expected {expected_name}, got {archive.name}")

    with zipfile.ZipFile(archive, "r") as zf:
        names = normalized_names(zf, prefix)
        for required in REQUIRED_FILES:
            if required not in names:
                errors.append(f"required file missing from release: {required}")
        roots = {name.split("/", 1)[0] for name in names if name}
        for forbidden in FORBIDDEN_ROOTS:
            if forbidden in roots:
                errors.append(f"forbidden root present in release: {forbidden}")
        for name in names:
            basename = Path(name).name
            if basename in FORBIDDEN_RELEASE_FILENAMES:
                errors.append(f"legacy private paper/figure file present in release: {name}")
            if Path(name).suffix.lower() == ".tex" and name not in ALLOWED_TEX_FILES:
                errors.append(f"unexpected TeX file present in release: {name}")
        for bad in scan_private_patterns(zf, prefix):
            errors.append(f"private database residue: {bad}")

        dockerfile_name = f"{prefix}/Dockerfile" if prefix else "Dockerfile"
        start_name = f"{prefix}/scripts/start_linux.sh" if prefix else "scripts/start_linux.sh"
        one_click_name = f"{prefix}/scripts/one_click_linux.sh" if prefix else "scripts/one_click_linux.sh"
        compose_name = f"{prefix}/docker-compose.yml" if prefix else "docker-compose.yml"
        env_name = f"{prefix}/.env.example" if prefix else ".env.example"
        service_name = f"{prefix}/boyuesql_service.py" if prefix else "boyuesql_service.py"
        dockerfile = zf.read(dockerfile_name).decode("utf-8", errors="ignore")
        start_script = zf.read(start_name).decode("utf-8", errors="ignore")
        one_click = zf.read(one_click_name).decode("utf-8", errors="ignore")
        compose = zf.read(compose_name).decode("utf-8", errors="ignore")
        env_example = zf.read(env_name).decode("utf-8", errors="ignore")
        service = zf.read(service_name).decode("utf-8", errors="ignore")
        if "APP_ENTRY=boyuesql_service.py" not in dockerfile:
            errors.append("Dockerfile does not default APP_ENTRY to boyuesql_service.py")
        if 'APP_ENTRY="${APP_ENTRY:-boyuesql_service.py}"' not in start_script:
            errors.append("start_linux.sh does not default APP_ENTRY to boyuesql_service.py")
        env_assignments = {
            line.strip().lower()
            for line in env_example.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        if "boyuesql_dialect=oracle" in env_assignments:
            errors.append(".env.example defaults to Oracle instead of a generic/local dialect")
        if 'default_dialect=os.environ.get("BOYUESQL_DIALECT", "oracle")' in service:
            errors.append("boyuesql_service.py defaults runtime_config to Oracle")
        for required_mode in (
            "models)",
            "plan)",
            "launch)",
            "resume)",
            "summarize)",
            "status)",
            "validate)",
            "evidence)",
            "abstract)",
            "bundle)",
            "diagnostics)",
            "paper-run)",
            "paper-launch)",
            "audit)",
            "run_models()",
            "run_plan()",
            "run_launch()",
            "run_resume()",
            "run_summarize()",
            "run_status()",
            "run_validate()",
            "run_evidence()",
            "run_abstract()",
            "run_bundle()",
            "run_diagnostics()",
            "run_paper_run()",
            "run_paper_launch()",
            "run_audit()",
        ):
            if required_mode not in one_click:
                errors.append(f"one_click_linux.sh missing server handoff mode/function: {required_mode}")
        if "OLLAMA_BASE_URL" not in compose or "OLLAMA_API_URL" in compose:
            errors.append("docker-compose.yml does not use OLLAMA_BASE_URL cleanly")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a clean BoyueSQL server release package.")
    parser.add_argument("--archive", default=str(PROJECT_ROOT / "artifacts" / "server_release" / "boyuesql_spider2_server.zip"))
    parser.add_argument("--checksum", default=str(PROJECT_ROOT / "artifacts" / "server_release" / "boyuesql_spider2_server.sha256"))
    parser.add_argument("--prefix", default="boyuesql_spider2_server")
    args = parser.parse_args()

    errors = verify_archive(Path(args.archive), Path(args.checksum), args.prefix)
    if errors:
        print("release verification failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"release verification passed: {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
