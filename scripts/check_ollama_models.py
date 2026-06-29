from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Iterable


def split_models(values: Iterable[str]) -> list[str]:
    models: list[str] = []
    for value in values:
        for part in str(value).split(","):
            name = part.strip()
            if name and name not in models:
                models.append(name)
    return models


def fetch_models(base_url: str, timeout: float) -> set[str]:
    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not connect to Ollama at {url}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON from {url}") from exc

    names: set[str] = set()
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        for key in ("name", "model"):
            value = item.get(key)
            if value:
                names.add(str(value))
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that required Ollama models are available before a benchmark run.")
    parser.add_argument("--base-url", default="http://localhost:11434", help="Ollama base URL.")
    parser.add_argument("--model", action="append", default=[], help="Required model name or comma-separated model list.")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds.")
    parser.add_argument("--warn-only", action="store_true", help="Print missing models but exit successfully.")
    args = parser.parse_args()

    required = split_models(args.model)
    if not required:
        print("No models requested; nothing to check.")
        return 0

    try:
        available = fetch_models(args.base_url, args.timeout)
    except RuntimeError as exc:
        print(f"[ollama-check] ERROR: {exc}", file=sys.stderr)
        return 0 if args.warn_only else 2

    missing = [model for model in required if model not in available]
    print(f"[ollama-check] endpoint: {args.base_url.rstrip('/')}")
    print(f"[ollama-check] required: {', '.join(required)}")
    print(f"[ollama-check] available: {', '.join(sorted(available)) if available else '(none)'}")
    if missing:
        print(f"[ollama-check] missing: {', '.join(missing)}", file=sys.stderr)
        print("[ollama-check] pull missing models before running, e.g. `ollama pull <model>`.", file=sys.stderr)
        return 0 if args.warn_only else 2
    print("[ollama-check] all required models are available")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
