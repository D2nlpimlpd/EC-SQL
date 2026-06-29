from __future__ import annotations

import re
import unittest
from pathlib import Path

from scripts.verify_server_release import verify_archive


PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_ROOTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "artifacts",
    "baselines",
    "pdf_extracted_images",
    "pdf_page_renders",
    "third_party",
}

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".env",
    ".html",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

ALLOWED_PATTERN_FILES = {
    Path("scripts/verify_server_release.py"),
    Path("tests/test_private_cleanup.py"),
}

FORBIDDEN_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
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
]


def source_files() -> list[Path]:
    files: list[Path] = []
    for path in PROJECT_ROOT.rglob("*"):
        rel = path.relative_to(PROJECT_ROOT)
        if not path.is_file():
            continue
        if rel.parts and rel.parts[0] in EXCLUDED_ROOTS:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        files.append(rel)
    return files


class PrivateCleanupTests(unittest.TestCase):
    def test_legacy_private_demo_files_are_removed(self) -> None:
        removed_paths = [
            "main.py",
            "templates",
            "static",
            "test_api_sql_fix.py",
            "APP3_INTEGRATION_GUIDE.md",
            "REFACTORING_GUIDE.md",
            "REFACTORING_CHECKLIST.md",
            "app_3.zip",
            "app_6.zip",
            "app_9.zip",
            "run.ps1",
        ]

        existing = [path for path in removed_paths if (PROJECT_ROOT / path).exists()]
        self.assertEqual(existing, [])

    def test_project_source_has_no_private_schema_terms(self) -> None:
        violations: list[str] = []
        for rel in source_files():
            if rel in ALLOWED_PATTERN_FILES:
                continue
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8", errors="ignore")
            for pattern in FORBIDDEN_PATTERNS:
                if pattern.search(text):
                    violations.append(f"{rel.as_posix()}: {pattern.pattern}")
                    break

        self.assertEqual(violations, [])

    def test_default_requirements_do_not_force_oracle_driver(self) -> None:
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        oracle_requirements = (PROJECT_ROOT / "requirements-oracle.txt").read_text(encoding="utf-8")

        self.assertNotIn("oracledb", requirements.lower())
        self.assertIn("oracledb", oracle_requirements.lower())

    def test_release_verifier_blocks_private_paper_artifacts_and_oracle_defaults(self) -> None:
        verifier = (PROJECT_ROOT / "scripts" / "verify_server_release.py").read_text(encoding="utf-8")

        self.assertIn("FORBIDDEN_RELEASE_FILENAMES", verifier)
        self.assertIn("ase.tex", verifier)
        self.assertIn("boyuesql_icdm_merged_figures.tex", verifier)
        self.assertIn("figure1_boyuesql.pdf", verifier)
        self.assertIn("unexpected TeX file present in release", verifier)
        self.assertIn("boyuesql_dialect=oracle", verifier)
        self.assertIn("defaults runtime_config to Oracle", verifier)

    def test_server_release_has_no_private_schema_terms_when_present(self) -> None:
        archive = PROJECT_ROOT / "artifacts" / "server_release" / "boyuesql_spider2_server.zip"
        checksum = PROJECT_ROOT / "artifacts" / "server_release" / "boyuesql_spider2_server.sha256"
        if not archive.exists():
            self.skipTest("server release package has not been built")

        errors = verify_archive(archive, checksum, "boyuesql_spider2_server")

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
