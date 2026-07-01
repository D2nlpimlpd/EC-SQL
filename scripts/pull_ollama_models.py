from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_ollama_models import fetch_models, split_models


def pull_model(base_url: str, model: str, timeout: float) -> None:
    url = base_url.rstrip("/") + "/api/pull"
    payload = json.dumps({"name": model, "stream": False}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not pull {model!r} from {url}: {exc}") from exc
    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError:
        parsed = {"raw": body[:500]}
    if isinstance(parsed, dict) and parsed.get("error"):
        raise RuntimeError(f"Ollama failed to pull {model!r}: {parsed['error']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull required Ollama models for EC-SQL server benchmarks.")
    parser.add_argument("--base-url", default="http://localhost:11434", help="Ollama base URL.")
    parser.add_argument("--model", action="append", default=[], help="Model name or comma-separated model list.")
    parser.add_argument("--timeout", type=float, default=1800.0, help="Per-model pull timeout in seconds.")
    parser.add_argument("--check-timeout", type=float, default=10.0, help="Ollama tag-list timeout in seconds.")
    parser.add_argument("--force", action="store_true", help="Pull even when a model already appears in /api/tags.")
    parser.add_argument("--warn-only", action="store_true", help="Report failures but exit successfully.")
    args = parser.parse_args()

    models = split_models(args.model)
    if not models:
        print("[ollama-pull] no models requested")
        return 0

    try:
        available = fetch_models(args.base_url, args.check_timeout)
    except RuntimeError as exc:
        print(f"[ollama-pull] ERROR: {exc}", file=sys.stderr)
        return 0 if args.warn_only else 2

    failures: list[str] = []
    for model in models:
        if model in available and not args.force:
            print(f"[ollama-pull] already available: {model}")
            continue
        print(f"[ollama-pull] pulling: {model}")
        try:
            pull_model(args.base_url, model, args.timeout)
            print(f"[ollama-pull] ready: {model}")
            available.add(model)
        except RuntimeError as exc:
            failures.append(str(exc))
            print(f"[ollama-pull] ERROR: {exc}", file=sys.stderr)
            if not args.warn_only:
                break

    if failures:
        return 0 if args.warn_only else 2
    print("[ollama-pull] all requested models are ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
