from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verify_server_release import FORBIDDEN_RELEASE_FILENAMES, PRIVATE_DB_PATTERNS


SKIP_ROOTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "artifacts",
    "baselines",
    "third_party",
}

ALLOWLIST_FILES = {
    "scripts/verify_server_release.py",
    "scripts/audit_workspace_residue.py",
    "tests/test_private_cleanup.py",
}

TEXT_SUFFIXES = {
    "",
    ".bib",
    ".cfg",
    ".csv",
    ".env",
    ".json",
    ".md",
    ".py",
    ".ps1",
    ".sh",
    ".svg",
    ".tex",
    ".txt",
    ".toml",
    ".yml",
    ".yaml",
}


@dataclass
class Finding:
    path: str
    reason: str
    match: str


def is_text_candidate(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return path.name in {"Dockerfile", ".env.example", ".gitignore", ".dockerignore"}


def should_skip(path: Path, root: Path) -> bool:
    rel = path.relative_to(root).as_posix()
    first = rel.split("/", 1)[0]
    return first in SKIP_ROOTS or rel in ALLOWLIST_FILES


def compiled_patterns() -> tuple[list[re.Pattern[str]], list[str]]:
    regexes = [re.compile(pattern, re.IGNORECASE) for pattern in PRIVATE_DB_PATTERNS if pattern.startswith(r"\b")]
    literals = [pattern for pattern in PRIVATE_DB_PATTERNS if not pattern.startswith(r"\b")]
    return regexes, literals


def scan_workspace(root: Path) -> list[Finding]:
    regexes, literals = compiled_patterns()
    findings: list[Finding] = []
    for path in root.rglob("*"):
        if not path.is_file() or should_skip(path, root):
            continue
        rel = path.relative_to(root).as_posix()
        if path.name in FORBIDDEN_RELEASE_FILENAMES:
            findings.append(Finding(rel, "legacy_private_filename", path.name))
        if not is_text_candidate(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in regexes:
            if pattern.search(text):
                findings.append(Finding(rel, "private_database_pattern", pattern.pattern))
        for literal in literals:
            if literal and literal in text:
                findings.append(Finding(rel, "private_database_literal", literal))
    return findings


def write_report(findings: list[Finding], out_json: Path, out_md: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "PASS" if not findings else "RESIDUE_FOUND",
        "finding_count": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Workspace Private Residue Report",
        "",
        f"Status: **{payload['status']}**",
        f"Finding count: {len(findings)}",
        "",
        "| Path | Reason | Match |",
        "| --- | --- | --- |",
    ]
    for finding in findings:
        lines.append(f"| {finding.path} | {finding.reason} | `{finding.match}` |")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def quarantine_findings(root: Path, findings: list[Finding], target: Path) -> list[str]:
    moved: list[str] = []
    unique_paths = sorted({finding.path for finding in findings})
    target.mkdir(parents=True, exist_ok=True)
    for rel in unique_paths:
        src = root / rel
        if not src.exists() or not src.is_file():
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(rel)
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description="Report legacy private database residue outside the clean server release allowlist.")
    parser.add_argument("--root", default=str(PROJECT_ROOT))
    parser.add_argument("--out-json", default=str(PROJECT_ROOT / "artifacts" / "workspace_private_residue_report.json"))
    parser.add_argument("--out-md", default=str(PROJECT_ROOT / "artifacts" / "workspace_private_residue_report.md"))
    parser.add_argument(
        "--quarantine",
        action="store_true",
        help="Move matching legacy files into artifacts/legacy_workspace_residue instead of only reporting.",
    )
    parser.add_argument(
        "--quarantine-dir",
        default=str(PROJECT_ROOT / "artifacts" / "legacy_workspace_residue"),
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()
    findings = scan_workspace(root)
    moved: list[str] = []
    if args.quarantine and findings:
        moved = quarantine_findings(root, findings, Path(args.quarantine_dir).resolve())
        findings = scan_workspace(root)
    write_report(findings, Path(args.out_json), Path(args.out_md))
    print(f"[workspace-residue] findings={len(findings)} report={args.out_md}")
    if moved:
        print(f"[workspace-residue] quarantined={len(moved)} dir={args.quarantine_dir}")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
