"""Dataset download and inspection helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional


SPIDER2_REPO_URL = "https://github.com/xlang-ai/Spider2.git"
SPIDER2_LOCALDB_GDRIVE_ID = "1coEVsCZq-Xvj9p2TnhBFoFTsY-UoYGmG"
SPIDER2_DBT_START_DB_GDRIVE_ID = "1N3f7BSWC4foj-V-1C9n8M2XmgV7FOcqL"
SPIDER2_DBT_GOLD_GDRIVE_ID = "1s0USV_iQLo4oe05QqAMnhGGp5jeejCzp"
SPIDER2_LOCALDB_URL = (
    "https://drive.usercontent.google.com/download?"
    + urllib.parse.urlencode(
        {
            "id": SPIDER2_LOCALDB_GDRIVE_ID,
            "export": "download",
            "authuser": "0",
        }
    )
)


def _gdrive_url(file_id: str) -> str:
    return (
        "https://drive.usercontent.google.com/download?"
        + urllib.parse.urlencode({"id": file_id, "export": "download", "authuser": "0"})
    )


@dataclass(frozen=True)
class DatasetStatus:
    name: str
    root: Path
    exists: bool
    files: Dict[str, int]
    notes: str = ""


def run(cmd: list[str], cwd: Optional[Path] = None) -> None:
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def ensure_git() -> None:
    if shutil.which("git") is None:
        raise RuntimeError("git is required to download Spider2 but was not found in PATH")


def ensure_spider2(
    dataset_root: str | Path,
    repo_url: str = SPIDER2_REPO_URL,
    force: bool = False,
    dry_run: bool = False,
) -> Path:
    """Clone or update Spider2 under dataset_root.

    The repository metadata can be downloaded without cloud credentials.  Full
    Spider2-Lite/Snow execution may still require BigQuery or Snowflake access;
    Spider2-DBT is the local no-cost subset described by the upstream project.
    """

    ensure_git()
    root = Path(dataset_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    dest = root / "Spider2"
    if dry_run:
        print(f"Spider2 target: {dest}")
        print(f"Repository: {repo_url}")
        return dest
    if dest.exists() and force:
        shutil.rmtree(dest)
    if (dest / ".git").exists():
        run(["git", "pull", "--ff-only"], cwd=dest)
    elif dest.exists() and any(dest.iterdir()):
        raise RuntimeError(f"{dest} exists and is not an empty Spider2 git repository")
    else:
        run(["git", "clone", "--depth", "1", repo_url, str(dest)])
    return dest


def ensure_spider2_localdb(
    spider2_root: str | Path,
    url: str = SPIDER2_LOCALDB_URL,
    force: bool = False,
    dry_run: bool = False,
) -> Path:
    """Download the optional Spider2-Lite SQLite local database archive.

    Upstream hosts this archive on Google Drive.  Some networks require manual
    browser confirmation; in that case this function leaves a clear error and
    the caller can use the printed URL manually.
    """

    root = Path(spider2_root).expanduser().resolve()
    if root.name.lower() != "spider2":
        root = root / "Spider2"
    localdb_dir = root / "spider2-lite" / "resource" / "databases" / "spider2-localdb"
    archive = root / "spider2-lite" / "resource" / "databases" / "spider2-localdb.zip"
    if dry_run:
        print(f"Spider2 localdb target: {localdb_dir}")
        print(f"Archive: {archive}")
        print(f"URL: {url}")
        return localdb_dir
    localdb_dir.mkdir(parents=True, exist_ok=True)
    if any(localdb_dir.glob("*.sqlite")) and not force:
        return localdb_dir
    if force and archive.exists():
        archive.unlink()
    if not archive.exists():
        print(f"Downloading Spider2 local SQLite DB archive from {url}")
        try:
            import gdown  # type: ignore

            gdown.download(id=SPIDER2_LOCALDB_GDRIVE_ID, output=str(archive), quiet=False)
        except Exception:
            try:
                urllib.request.urlretrieve(url, archive)
            except Exception as exc:
                raise RuntimeError(
                    "Could not download Spider2 local DB archive automatically. "
                    f"Open this URL manually and save it to {archive}: {url}"
                ) from exc
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(localdb_dir)
    else:
        try:
            archive.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"Downloaded file is not a ZIP archive: {archive}. "
            "Google Drive may have returned a confirmation HTML page."
        )
    return localdb_dir


def _download_gdrive_file(file_id: str, output: Path, dry_run: bool = False) -> None:
    url = _gdrive_url(file_id)
    if dry_run:
        print(f"Google Drive file {file_id} -> {output}")
        print(f"URL: {url}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.stat().st_size > 0:
        return
    print(f"Downloading Google Drive file {file_id} to {output}")
    try:
        import gdown  # type: ignore

        gdown.download(id=file_id, output=str(output), quiet=False)
    except Exception:
        try:
            urllib.request.urlretrieve(url, output)
        except Exception as exc:
            raise RuntimeError(
                f"Could not download Google Drive file {file_id}. "
                f"Open this URL manually and save it to {output}: {url}"
            ) from exc


def ensure_spider2_dbt_databases(
    spider2_root: str | Path,
    force: bool = False,
    dry_run: bool = False,
    run_setup: bool = True,
) -> Path:
    """Download and install the optional Spider2-DBT DuckDB archives."""

    root = Path(spider2_root).expanduser().resolve()
    if root.name.lower() != "spider2":
        root = root / "Spider2"
    dbt_root = root / "spider2-dbt"
    start_archive = dbt_root / "DBT_start_db.zip"
    gold_archive = dbt_root / "dbt_gold.zip"
    if dry_run:
        _download_gdrive_file(SPIDER2_DBT_START_DB_GDRIVE_ID, start_archive, dry_run=True)
        _download_gdrive_file(SPIDER2_DBT_GOLD_GDRIVE_ID, gold_archive, dry_run=True)
        return dbt_root
    if force:
        for archive in (start_archive, gold_archive):
            if archive.exists():
                archive.unlink()
    _download_gdrive_file(SPIDER2_DBT_START_DB_GDRIVE_ID, start_archive)
    _download_gdrive_file(SPIDER2_DBT_GOLD_GDRIVE_ID, gold_archive)
    for archive in (start_archive, gold_archive):
        if not zipfile.is_zipfile(archive):
            try:
                archive.unlink()
            except OSError:
                pass
            raise RuntimeError(f"Downloaded DBT archive is not a ZIP file: {archive}")
    if run_setup:
        run([sys.executable, "setup.py"], cwd=dbt_root)
    return dbt_root


def count_jsonl(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except (FileNotFoundError, UnicodeDecodeError):
        return 0


def inspect_spider2(root: str | Path) -> DatasetStatus:
    repo = Path(root).expanduser().resolve()
    if repo.name.lower() != "spider2":
        repo = repo / "Spider2"
    files: Dict[str, int] = {}
    for jsonl in repo.rglob("*.jsonl"):
        rel = jsonl.relative_to(repo).as_posix()
        files[rel] = count_jsonl(jsonl)
    if not files:
        for json_file in repo.rglob("*.json"):
            rel = json_file.relative_to(repo).as_posix()
            try:
                payload = json.loads(json_file.read_text(encoding="utf-8"))
                files[rel] = len(payload) if isinstance(payload, list) else 1
            except Exception:
                continue
    sqlite_dir = repo / "spider2-lite" / "resource" / "databases" / "spider2-localdb"
    sqlite_count = len(list(sqlite_dir.rglob("*.sqlite"))) if sqlite_dir.exists() else 0
    if sqlite_count:
        files["spider2-lite/resource/databases/spider2-localdb/*.sqlite"] = sqlite_count
    dbt_duckdb_count = len(list((repo / "spider2-dbt").rglob("*.duckdb"))) if (repo / "spider2-dbt").exists() else 0
    if dbt_duckdb_count:
        files["spider2-dbt/**/*.duckdb"] = dbt_duckdb_count
    notes = (
        "Spider2 metadata is local. Lite/Snow execution may require BigQuery or "
        "Snowflake credentials; DBT is the no-cost DuckDB subset."
    )
    return DatasetStatus(name="Spider2", root=repo, exists=repo.exists(), files=files, notes=notes)


def iter_status_lines(status: DatasetStatus) -> Iterable[str]:
    yield f"name: {status.name}"
    yield f"root: {status.root}"
    yield f"exists: {status.exists}"
    if status.files:
        yield "files:"
        for name, count in sorted(status.files.items()):
            yield f"  {name}: {count}"
    else:
        yield "files: none found yet"
    if status.notes:
        yield f"notes: {status.notes}"
