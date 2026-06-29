from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def candidate_bashes() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("BASH_EXE")
    if env_path:
        candidates.append(Path(env_path))
    if os.name == "nt":
        candidates.extend(
            [
                Path(r"C:\Program Files\Git\bin\bash.exe"),
                Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
                Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
            ]
        )
    path_bash = shutil.which("bash")
    if path_bash:
        candidates.append(Path(path_bash))

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def usable_bash(path: Path) -> bool:
    if not path.exists() and path.parent != Path("."):
        return False
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except Exception:
        return False
    text = f"{completed.stdout}\n{completed.stderr}".lower()
    return completed.returncode == 0 and "bash" in text


def find_bash(explicit: str = "") -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if usable_bash(path) else None
    for path in candidate_bashes():
        if usable_bash(path):
            return path
    return None


def default_shell_scripts(project_root: Path = PROJECT_ROOT) -> list[Path]:
    return sorted((project_root / "scripts").glob("*.sh"))


def check_shell_scripts(
    scripts: list[Path] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    bash: str = "",
) -> tuple[Path | None, list[str]]:
    bash_path = find_bash(bash)
    if not bash_path:
        return None, ["no usable bash executable found; set BASH_EXE to Git Bash or GNU bash"]

    errors: list[str] = []
    for script in scripts or default_shell_scripts(project_root):
        completed = subprocess.run(
            [str(bash_path), "-n", str(script)],
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            errors.append(f"{script}: {detail}")
    return bash_path, errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bash -n over BoyueSQL Linux shell scripts.")
    parser.add_argument("scripts", nargs="*", help="Optional shell scripts. Defaults to scripts/*.sh.")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--bash", default=os.environ.get("BASH_EXE", ""))
    parser.add_argument("--warn-only", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    scripts = [Path(item) for item in args.scripts] if args.scripts else default_shell_scripts(project_root)
    bash_path, errors = check_shell_scripts(scripts, project_root=project_root, bash=args.bash)
    if errors:
        print("[shell-syntax] FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 0 if args.warn_only else 2
    print(f"[shell-syntax] PASS bash={bash_path}, scripts={len(scripts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
