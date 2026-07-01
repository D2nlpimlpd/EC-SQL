from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable


DEFAULT_HF_MODELS = (
    "NumbersStation/nsql-6B,"
    "deepseek-ai/deepseek-coder-6.7b-instruct"
)

DEFAULT_IGNORE_PATTERNS = [
    "*.msgpack",
    "*.h5",
    "*.ot",
]


def split_models(values: Iterable[str]) -> list[str]:
    models: list[str] = []
    for value in values:
        for part in str(value).split(","):
            name = part.strip()
            if name and name not in models:
                models.append(name)
    return models


def model_values_from_env() -> list[str]:
    values: list[str] = []
    if os.environ.get("HF_BASELINE_MODELS"):
        values.append(os.environ["HF_BASELINE_MODELS"])
    else:
        values.append(DEFAULT_HF_MODELS)
    if os.environ.get("HF_EXTRA_MODELS"):
        values.append(os.environ["HF_EXTRA_MODELS"])
    return values


def load_snapshot_download():
    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:  # pragma: no cover - exercised on minimal servers only
        raise RuntimeError(
            "huggingface_hub is not installed. Run `python -m pip install -r "
            "requirements-server.txt` first."
        ) from exc
    return snapshot_download


def download_model(
    *,
    model_id: str,
    cache_dir: str | None,
    revision: str | None,
    token: str | None,
    allow_patterns: list[str] | None,
    ignore_patterns: list[str] | None,
    local_files_only: bool,
) -> str:
    snapshot_download = load_snapshot_download()
    kwargs = {
        "repo_id": model_id,
        "revision": revision,
        "cache_dir": cache_dir,
        "token": token,
        "allow_patterns": allow_patterns or None,
        "ignore_patterns": ignore_patterns or None,
        "local_files_only": local_files_only,
        "resume_download": True,
    }
    kwargs = {key: value for key, value in kwargs.items() if value is not None}
    return str(snapshot_download(**kwargs))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download HuggingFace model snapshots for EC-SQL server baselines."
    )
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="HF repo id or comma-separated repo ids. Defaults to HF_BASELINE_MODELS plus HF_EXTRA_MODELS.",
    )
    parser.add_argument("--cache-dir", default=os.environ.get("HF_HOME", ""))
    parser.add_argument("--revision", default="")
    parser.add_argument("--token", default=os.environ.get("HF_TOKEN", ""))
    parser.add_argument("--allow-pattern", action="append", default=[])
    parser.add_argument("--ignore-pattern", action="append", default=[])
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--warn-only", action="store_true")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    raw_values = args.model if args.model else model_values_from_env()
    models = split_models(raw_values)
    if not models:
        print("[hf-download] no HuggingFace models requested")
        return 0

    ignore_patterns = args.ignore_pattern or DEFAULT_IGNORE_PATTERNS
    cache_dir = args.cache_dir or None
    revision = args.revision or None
    token = args.token or None
    allow_patterns = args.allow_pattern or None

    rows: list[dict[str, str]] = []
    failures: list[str] = []
    print(f"[hf-download] requested: {', '.join(models)}")
    if cache_dir:
        print(f"[hf-download] cache_dir: {cache_dir}")
    if args.dry_run:
        for model in models:
            rows.append({"model": model, "status": "DRY_RUN", "path": ""})
            print(f"[hf-download] dry-run: {model}")
    else:
        for model in models:
            print(f"[hf-download] downloading: {model}")
            try:
                path = download_model(
                    model_id=model,
                    cache_dir=cache_dir,
                    revision=revision,
                    token=token,
                    allow_patterns=allow_patterns,
                    ignore_patterns=ignore_patterns,
                    local_files_only=args.local_files_only,
                )
                rows.append({"model": model, "status": "PASS", "path": path})
                print(f"[hf-download] ready: {model} -> {path}")
            except Exception as exc:
                message = f"{model}: {type(exc).__name__}: {exc}"
                rows.append({"model": model, "status": "FAIL", "path": "", "error": str(exc)})
                failures.append(message)
                print(f"[hf-download] ERROR: {message}", file=sys.stderr)
                if not args.warn_only:
                    break

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"models": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[hf-download] json: {out}")

    if failures:
        return 0 if args.warn_only else 2
    print("[hf-download] all requested HF snapshots are ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
